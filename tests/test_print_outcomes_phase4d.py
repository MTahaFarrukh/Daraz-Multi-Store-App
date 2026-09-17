"""Phase 4D — print outcomes, hydrate missing items, no silent skips."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from pypdf import PdfWriter

from src.db import reset_repo_for_tests
from src.label_processor import LabelDocument
from src.ops import print_labels_for_orders
from src.print_outcomes import (
    ALREADY_PRINTED,
    DOCUMENT_FAILED,
    PACKAGE_RESOLUTION_FAILED,
    SUCCESS,
)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / ".token_key"
    key_path.write_text(key, encoding="utf-8")
    monkeypatch.setenv("AUTH_TEST_MODE", "true")
    monkeypatch.setenv("TENANCY_REPO", "memory")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DARAZ_APP_KEY", "appkey")
    monkeypatch.setenv("DARAZ_APP_SECRET", "appsecret")
    # Per-order fetch path under test; native/bulk covered separately.
    monkeypatch.setenv("PRINT_PREFER_NATIVE_PDF", "0")
    monkeypatch.setenv("PRINT_PREFER_BULK_GETDOCUMENT", "0")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    return reset_repo_for_tests()


def _store(repo, wid: str, slug: str = "s1") -> dict:
    return repo.upsert_store(
        wid,
        {
            "store_id": slug,
            "account": f"{slug}@x.com",
            "seller_id": f"seller-{slug}",
            "display_name": slug,
            "store_name": slug,
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 86400,
            "country": "pk",
        },
    )


def _order_header_only(repo, wid: str, store: dict, daraz_oid: str) -> dict:
    return repo.upsert_daraz_order(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_order_id": daraz_oid,
            "order_number": f"N-{daraz_oid}",
            "status_raw": "ready_to_ship",
            "status_group": "ready_to_ship",
            "statuses": ["ready_to_ship"],
        }
    )


def _order_with_item(repo, wid: str, store: dict, daraz_oid: str) -> dict:
    order = _order_header_only(repo, wid, store, daraz_oid)
    repo.upsert_daraz_order_item(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "order_id": order["id"],
            "daraz_order_item_id": f"item-{daraz_oid}",
            "daraz_order_id": daraz_oid,
            "status_raw": "ready_to_ship",
            "package_id": f"pkg-{daraz_oid}",
            "name": "Thing",
            "sku": "SKU1",
            "quantity": 1,
        }
    )
    return order


def _pdf_label(order_id: str = "111") -> LabelDocument:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return LabelDocument(
        store_id="s1",
        store_name="S1",
        order_id=order_id,
        order_item_id="item-1",
        source_filename=f"{order_id}__item.pdf",
        mime_type="application/pdf",
        document_bytes=buf.getvalue(),
    )


def test_hydrate_then_print_orders_without_items(repo, tmp_path, monkeypatch) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    order = _order_header_only(repo, wid, store, "700")
    assert repo.list_order_items(wid, order["id"]) == []

    mock_client = MagicMock()
    mock_client.get_multiple_order_items.return_value = {
        "data": [
            {
                "order_id": "700",
                "order_items": [
                    {
                        "order_item_id": "item-700",
                        "status": "ready_to_ship",
                        "package_id": "pkg-700",
                        "name": "Hydrated",
                        "sku": "SKU-H",
                    }
                ],
            }
        ]
    }
    # hydrate_missing_order_items imports client_for_store from src.ops
    monkeypatch.setattr("src.ops.client_for_store", lambda s: mock_client)
    monkeypatch.setattr(
        "src.ops._fetch_label_document",
        lambda *a, **k: (_pdf_label("700"), "print_awb_pdf", {"package_id": "pkg-700"}),
    )

    result = print_labels_for_orders(
        wid,
        "u1",
        [order["id"]],
        allow_reprint=False,
        output=tmp_path / "out.pdf",
    )
    assert result["print_status"] == "success"
    assert result["summary"]["success_count"] == 1
    assert result["outcomes"][0]["state"] == SUCCESS
    assert repo.list_order_items(wid, order["id"]), "items should be hydrated"
    assert result["hydrate"]["orders_hydrated"] == 1
    prints = repo.list_label_prints_for_orders(wid, [order["id"]])[order["id"]]
    assert len(prints) == 1


def test_partial_success_two_of_three(repo, tmp_path, monkeypatch) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    o1 = _order_with_item(repo, wid, store, "801")
    o2 = _order_with_item(repo, wid, store, "802")
    o3 = _order_with_item(repo, wid, store, "803")

    def fetch(_client, *, order_id, **_k):
        if str(order_id) == "803":
            raise RuntimeError("document boom")
        return (
            _pdf_label(str(order_id)),
            "print_awb_pdf",
            {"package_id": f"pkg-{order_id}"},
        )

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)
    monkeypatch.setattr("src.ops.client_for_store", lambda s: MagicMock())

    result = print_labels_for_orders(
        wid,
        "u1",
        [o1["id"], o2["id"], o3["id"]],
        allow_reprint=False,
        output=tmp_path / "out.pdf",
    )
    assert result["print_status"] == "partial_success"
    assert result["message"] == "Print completed: 2 / 3"
    assert result["failed_order_ids"] == [o3["id"]]
    assert result["summary"]["success_count"] == 2
    by_id = {o["order_id"]: o for o in result["outcomes"]}
    assert by_id[o1["id"]]["state"] == SUCCESS
    assert by_id[o2["id"]]["state"] == SUCCESS
    assert by_id[o3["id"]]["state"] == DOCUMENT_FAILED


def test_failed_targets_do_not_get_print_events(repo, tmp_path, monkeypatch) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    ok = _order_with_item(repo, wid, store, "901")
    bad = _order_with_item(repo, wid, store, "902")

    def fetch(_client, *, order_id, **_k):
        if str(order_id) == "902":
            raise RuntimeError("fail fetch")
        return (
            _pdf_label(str(order_id)),
            "get_document_pdf",
            {"package_id": "pkg-901"},
        )

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)
    monkeypatch.setattr("src.ops.client_for_store", lambda s: MagicMock())

    result = print_labels_for_orders(
        wid,
        "u1",
        [ok["id"], bad["id"]],
        allow_reprint=False,
        output=tmp_path / "out.pdf",
    )
    assert result["print_status"] == "partial_success"
    prints_ok = repo.list_label_prints_for_orders(wid, [ok["id"]])[ok["id"]]
    prints_bad = repo.list_label_prints_for_orders(wid, [bad["id"]])[bad["id"]]
    assert len(prints_ok) == 1
    assert prints_bad == []
    assert repo.has_label_print(wid, store["id"], "902") is False


def test_already_printed_without_reprint_is_outcome(repo, tmp_path, monkeypatch) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    order = _order_with_item(repo, wid, store, "333")
    repo.insert_label_print(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "order_id": order["id"],
            "daraz_order_id": "333",
            "order_item_ids": ["item-333"],
            "is_reprint": False,
        }
    )
    fetch_called = {"n": 0}

    def fetch(*_a, **_k):
        fetch_called["n"] += 1
        return (_pdf_label("333"), "print_awb_pdf", {"package_id": "pkg"})

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)
    monkeypatch.setattr("src.ops.client_for_store", lambda s: MagicMock())

    result = print_labels_for_orders(
        wid,
        "u1",
        [order["id"]],
        allow_reprint=False,
        output=tmp_path / "out.pdf",
    )
    assert fetch_called["n"] == 0
    assert result["summary"]["selected_count"] == 1
    assert result["outcomes"][0]["state"] == ALREADY_PRINTED
    assert result["message"] == "Print completed: 0 / 1"
    # Original print event only — no new record
    prints = repo.list_label_prints_for_orders(wid, [order["id"]])[order["id"]]
    assert len(prints) == 1


def test_empty_item_ids_after_hydrate_is_package_resolution_failed(
    repo, tmp_path, monkeypatch
) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    order = _order_header_only(repo, wid, store, "555")

    mock_client = MagicMock()
    # API returns empty items — hydrate writes nothing useful
    mock_client.get_multiple_order_items.return_value = {
        "data": [{"order_id": "555", "order_items": []}]
    }
    monkeypatch.setattr("src.ops.client_for_store", lambda s: mock_client)

    fetch_called = {"n": 0}

    def fetch(*_a, **_k):
        fetch_called["n"] += 1
        return (_pdf_label("555"), "print_awb_pdf", {"package_id": "pkg"})

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)

    result = print_labels_for_orders(
        wid,
        "u1",
        [order["id"]],
        allow_reprint=False,
        output=tmp_path / "out.pdf",
    )
    assert fetch_called["n"] == 0
    assert len(result["outcomes"]) == 1
    assert result["outcomes"][0]["state"] == PACKAGE_RESOLUTION_FAILED
    assert result["print_status"] == "failed"
    assert result["failed_order_ids"] == [order["id"]]
    prints = repo.list_label_prints_for_orders(wid, [order["id"]])[order["id"]]
    assert prints == []


def _bulk_pdf_resp(pages: int = 2) -> dict:
    import base64

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return {
        "code": "0",
        "data": {
            "document": {
                "file": encoded,
                "mime_type": "application/pdf",
            }
        },
    }


def _bulk_html_resp() -> dict:
    import base64

    html = b"<html><body>label</body></html>"
    encoded = base64.b64encode(html).decode("ascii")
    return {
        "code": "0",
        "data": {
            "document": {
                "file": encoded,
                "mime_type": "text/html",
            }
        },
    }


def _print_awb_pdf_resp() -> dict:
    import base64

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return {
        "code": "0",
        "data": {
            "file": encoded,
            "doc_type": "PDF",
        },
    }


def test_bulk_getdocument_one_call_for_multiple_orders(
    repo, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PRINT_PREFER_NATIVE_PDF", "0")
    monkeypatch.setenv("PRINT_PREFER_BULK_GETDOCUMENT", "1")
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    o1 = _order_with_item(repo, wid, store, "B1")
    o2 = _order_with_item(repo, wid, store, "B2")

    mock_client = MagicMock()
    mock_client.get_shipping_label.return_value = _bulk_pdf_resp(pages=2)
    monkeypatch.setattr("src.ops.client_for_store", lambda s: mock_client)

    fetch_called = {"n": 0}

    def fetch(*_a, **_k):
        fetch_called["n"] += 1
        raise AssertionError("fallback must not run on bulk success")

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)

    result = print_labels_for_orders(
        wid,
        "u1",
        [o1["id"], o2["id"]],
        allow_reprint=False,
        output=tmp_path / "bulk.pdf",
    )
    assert fetch_called["n"] == 0
    assert mock_client.get_shipping_label.call_count == 1
    ids_arg = mock_client.get_shipping_label.call_args.args[0]
    assert set(ids_arg) == {"item-B1", "item-B2"}
    assert result["print_status"] == "success"
    assert result["summary"]["success_count"] == 2
    assert result["timings_ms"]["getdocument_calls"] == 1
    assert result["timings_ms"]["bulk_calls"] == 1
    assert result["timings_ms"]["printawb_calls"] == 0
    assert result["timings_ms"]["html_docs_converted"] == 0
    assert all(o["state"] == SUCCESS for o in result["outcomes"])
    assert all(
        d.get("fetch_source") == "get_document_bulk" for d in result["label_details"]
    )
    prints1 = repo.list_label_prints_for_orders(wid, [o1["id"]])[o1["id"]]
    prints2 = repo.list_label_prints_for_orders(wid, [o2["id"]])[o2["id"]]
    assert len(prints1) == 1
    assert len(prints2) == 1
    assert prints1[0].get("fetch_source") == "get_document_bulk"
    diag = result["document_diagnostics"][0]
    assert diag["bulk_attempted"] is True
    assert diag["bulk_success"] is True
    assert diag["bulk_format"] == "pdf"
    assert diag["pages"] == 2


def test_bulk_fallback_isolates_failures(repo, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PRINT_PREFER_NATIVE_PDF", "0")
    monkeypatch.setenv("PRINT_PREFER_BULK_GETDOCUMENT", "1")
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    o1 = _order_with_item(repo, wid, store, "F1")
    o2 = _order_with_item(repo, wid, store, "F2")

    from src.daraz_api import DarazApiError

    mock_client = MagicMock()
    mock_client.get_shipping_label.side_effect = DarazApiError("bulk fail", code="500")
    monkeypatch.setattr("src.ops.client_for_store", lambda s: mock_client)

    def fetch(_client, *, order_id, **_k):
        if str(order_id) == "F2":
            raise RuntimeError("per-order fail")
        return (
            _pdf_label(str(order_id)),
            "get_document_pdf",
            {"package_id": f"pkg-{order_id}", "print_awb_attempted": False},
        )

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)

    result = print_labels_for_orders(
        wid,
        "u1",
        [o1["id"], o2["id"]],
        allow_reprint=False,
        output=tmp_path / "fb.pdf",
    )
    # Binary-split: root + two halves (each size 1 fails API again) = 3 bulk attempts
    assert mock_client.get_shipping_label.call_count >= 1
    assert result["print_status"] == "partial_success"
    by_id = {o["order_id"]: o for o in result["outcomes"]}
    assert by_id[o1["id"]]["state"] == SUCCESS
    assert by_id[o2["id"]]["state"] == DOCUMENT_FAILED
    prints_ok = repo.list_label_prints_for_orders(wid, [o1["id"]])[o1["id"]]
    prints_bad = repo.list_label_prints_for_orders(wid, [o2["id"]])[o2["id"]]
    assert len(prints_ok) == 1
    assert prints_bad == []
    assert result["timings_ms"]["fallback_fetch_ms"] >= 0
    assert result["timings_ms"]["getdocument_calls"] >= 1


def test_bulk_print_events_only_on_success(repo, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PRINT_PREFER_NATIVE_PDF", "0")
    monkeypatch.setenv("PRINT_PREFER_BULK_GETDOCUMENT", "1")
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    order = _order_with_item(repo, wid, store, "OK1")

    mock_client = MagicMock()
    mock_client.get_shipping_label.return_value = _bulk_pdf_resp(pages=1)
    monkeypatch.setattr("src.ops.client_for_store", lambda s: mock_client)

    result = print_labels_for_orders(
        wid,
        "u1",
        [order["id"]],
        allow_reprint=False,
        output=tmp_path / "ok.pdf",
    )
    assert result["outcomes"][0]["state"] == SUCCESS
    prints = repo.list_label_prints_for_orders(wid, [order["id"]])[order["id"]]
    assert len(prints) == 1
    assert result["prints_recorded"] == 1


def test_native_printawb_used_when_package_id_present(
    repo, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PRINT_PREFER_NATIVE_PDF", "1")
    monkeypatch.setenv("PRINT_PREFER_BULK_GETDOCUMENT", "1")  # native wins
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    o1 = _order_with_item(repo, wid, store, "N1")
    o2 = _order_with_item(repo, wid, store, "N2")

    mock_client = MagicMock()
    mock_client.get_package_shipping_label.return_value = _print_awb_pdf_resp()
    mock_client.download_binary_url = MagicMock(side_effect=AssertionError("no url"))
    monkeypatch.setattr("src.ops.client_for_store", lambda s: mock_client)

    fetch_called = {"n": 0}

    def fetch(*_a, **_k):
        fetch_called["n"] += 1
        raise AssertionError("per-order fallback must not run")

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)

    result = print_labels_for_orders(
        wid,
        "u1",
        [o1["id"], o2["id"]],
        allow_reprint=False,
        output=tmp_path / "native.pdf",
    )
    assert fetch_called["n"] == 0
    assert mock_client.get_package_shipping_label.call_count == 2
    assert mock_client.get_shipping_label.call_count == 0
    assert result["print_status"] == "success"
    assert result["summary"]["success_count"] == 2
    assert result["timings_ms"]["printawb_calls"] == 2
    assert result["timings_ms"]["getdocument_calls"] == 0
    assert result["timings_ms"]["html_docs_converted"] == 0
    assert all(
        d.get("fetch_source") == "print_awb_pdf" for d in result["label_details"]
    )
    assert len(repo.list_label_prints_for_orders(wid, [o1["id"]])[o1["id"]]) == 1
    assert len(repo.list_label_prints_for_orders(wid, [o2["id"]])[o2["id"]]) == 1


def test_bulk_html_does_not_mark_all_success(repo, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PRINT_PREFER_NATIVE_PDF", "0")
    monkeypatch.setenv("PRINT_PREFER_BULK_GETDOCUMENT", "1")
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    o1 = _order_with_item(repo, wid, store, "H1")
    o2 = _order_with_item(repo, wid, store, "H2")

    mock_client = MagicMock()
    mock_client.get_shipping_label.return_value = _bulk_html_resp()
    monkeypatch.setattr("src.ops.client_for_store", lambda s: mock_client)

    def fetch(_client, *, order_id, **_k):
        return (
            _pdf_label(str(order_id)),
            "get_document_pdf",
            {"package_id": f"pkg-{order_id}", "print_awb_attempted": False},
        )

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)

    msgs: list[str] = []
    result = print_labels_for_orders(
        wid,
        "u1",
        [o1["id"], o2["id"]],
        allow_reprint=False,
        output=tmp_path / "html.pdf",
        on_progress=msgs.append,
    )
    assert result["print_status"] == "success"
    assert result["summary"]["success_count"] == 2
    # Bulk HTML must fall back per-order — not convert one HTML blob as 2 successes
    assert mock_client.get_shipping_label.call_count == 1
    diag = result["document_diagnostics"][0]
    assert diag["bulk_format"] == "html"
    assert diag["bulk_success"] is False
    assert diag["fallback_count"] == 2
    assert result["timings_ms"]["html_docs_converted"] == 0
    assert not any("Converting" in m and "HTML" in m for m in msgs)
    assert all(o["state"] == SUCCESS for o in result["outcomes"])
    assert len(result["outcomes"]) == 2


def test_bulk_pdf_page_mismatch_recovers_or_mapping_failed(
    repo, tmp_path, monkeypatch
) -> None:
    """14-page PDF for 3 orders must not silently SUCCESS-credit all three."""
    monkeypatch.setenv("PRINT_PREFER_NATIVE_PDF", "0")
    monkeypatch.setenv("PRINT_PREFER_BULK_GETDOCUMENT", "1")
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    orders = [
        _order_with_item(repo, wid, store, f"M{i}") for i in range(1, 4)
    ]

    mock_client = MagicMock()
    # Always return 1-page PDF for any bulk size → never matches multi-order groups
    mock_client.get_shipping_label.return_value = _bulk_pdf_resp(pages=1)
    monkeypatch.setattr("src.ops.client_for_store", lambda s: mock_client)

    def fetch(_client, *, order_id, **_k):
        return (
            _pdf_label(str(order_id)),
            "get_document_pdf",
            {"package_id": f"pkg-{order_id}", "print_awb_attempted": False},
        )

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)

    result = print_labels_for_orders(
        wid,
        "u1",
        [o["id"] for o in orders],
        allow_reprint=False,
        output=tmp_path / "mismatch.pdf",
    )
    # Per-order recovery after binary-split isolation
    assert result["summary"]["success_count"] == 3
    assert result["print_status"] == "success"
    assert all(o["state"] == SUCCESS for o in result["outcomes"])
    # Binary split should have attempted bulk more than once
    assert mock_client.get_shipping_label.call_count >= 2
    # Print events only on SUCCESS
    for o in orders:
        assert len(repo.list_label_prints_for_orders(wid, [o["id"]])[o["id"]]) == 1
    assert result["prints_recorded"] == 3
    assert result["timings_ms"]["html_docs_converted"] == 0


def test_page_reconcile_never_over_credits_print_events(
    repo, tmp_path, monkeypatch
) -> None:
    """Safety rail: fewer PDF pages than pending successes → DOCUMENT_MAPPING_FAILED."""
    from src.print_outcomes import DOCUMENT_MAPPING_FAILED

    monkeypatch.setenv("PRINT_PREFER_NATIVE_PDF", "0")
    monkeypatch.setenv("PRINT_PREFER_BULK_GETDOCUMENT", "0")
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    o1 = _order_with_item(repo, wid, store, "R1")
    o2 = _order_with_item(repo, wid, store, "R2")
    o3 = _order_with_item(repo, wid, store, "R3")

    # Two 1-page labels but we will force three pending by patching page count after merge
    def fetch(_client, *, order_id, **_k):
        return (
            _pdf_label(str(order_id)),
            "get_document_pdf",
            {"package_id": f"pkg-{order_id}", "print_awb_attempted": False},
        )

    monkeypatch.setattr("src.ops._fetch_label_document", fetch)
    monkeypatch.setattr("src.ops.client_for_store", lambda s: MagicMock())
    # Pretend merged PDF only has 2 pages while 3 successes pending
    monkeypatch.setattr("src.ops.pdf_page_count", lambda _p: 2)

    result = print_labels_for_orders(
        wid,
        "u1",
        [o1["id"], o2["id"], o3["id"]],
        allow_reprint=False,
        output=tmp_path / "reconcile.pdf",
    )
    assert len(result["outcomes"]) == 3
    by_id = {o["order_id"]: o for o in result["outcomes"]}
    states = {by_id[o1["id"]]["state"], by_id[o2["id"]]["state"], by_id[o3["id"]]["state"]}
    assert SUCCESS in states
    assert DOCUMENT_MAPPING_FAILED in states
    assert result["summary"]["success_count"] == 2
    assert result["prints_recorded"] == 2
    # Only SUCCESS orders get print events
    printed = 0
    for oid in (o1["id"], o2["id"], o3["id"]):
        printed += len(repo.list_label_prints_for_orders(wid, [oid])[oid])
    assert printed == 2
    failed = [o for o in result["outcomes"] if o["state"] == DOCUMENT_MAPPING_FAILED]
    assert len(failed) == 1
    assert not repo.list_label_prints_for_orders(wid, [failed[0]["order_id"]])[
        failed[0]["order_id"]
    ]