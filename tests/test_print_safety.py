"""Phase 3 print safety — exclude reprints, record events only after success."""

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
from src.print_safety import record_label_prints, validate_print_targets


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


def _order(repo, wid: str, store: dict, daraz_oid: str = "111") -> dict:
    order = repo.upsert_daraz_order(
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


def test_validate_splits_printed_and_new(repo) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    order = _order(repo, wid, store, "111")
    result = validate_print_targets(wid, [order["id"]])
    assert len(result["new_printable"]) == 1
    assert result["already_printed"] == []

    repo.insert_label_print(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "order_id": order["id"],
            "daraz_order_id": "111",
            "order_item_ids": ["item-111"],
            "is_reprint": False,
        }
    )
    result2 = validate_print_targets(wid, [order["id"]])
    assert result2["new_printable"] == []
    assert len(result2["already_printed"]) == 1


def test_print_excludes_without_allow_reprint(repo, tmp_path, monkeypatch) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    order = _order(repo, wid, store, "222")
    repo.insert_label_print(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "order_id": order["id"],
            "daraz_order_id": "222",
            "order_item_ids": ["item-222"],
        }
    )

    monkeypatch.setattr(
        "src.ops._fetch_label_document",
        lambda *a, **k: (_pdf_label("222"), "print_awb_pdf", {"package_id": "pkg"}),
    )
    monkeypatch.setattr("src.ops.client_for_store", lambda s: MagicMock())

    result = print_labels_for_orders(
        wid,
        "u1",
        [order["id"]],
        allow_reprint=False,
        output=tmp_path / "out.pdf",
    )
    assert result["print_status"] == "failed"
    assert result["outcomes"][0]["state"] == "ALREADY_PRINTED"
    assert result["labels"] == 0
    assert result["prints_recorded"] == 0


def test_allow_reprint_creates_is_reprint(repo, tmp_path, monkeypatch) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    order = _order(repo, wid, store, "333")
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

    monkeypatch.setattr(
        "src.ops._fetch_label_document",
        lambda *a, **k: (_pdf_label("333"), "get_document_pdf", {"package_id": "pkg"}),
    )
    monkeypatch.setattr("src.ops.client_for_store", lambda s: MagicMock())

    result = print_labels_for_orders(
        wid,
        "u1",
        [order["id"]],
        allow_reprint=True,
        job_id="job-1",
        output=tmp_path / "out.pdf",
    )
    assert result["reprint_count"] == 1
    assert result["new_labels_count"] == 0
    prints = repo.list_label_prints_for_orders(wid, [order["id"]])[order["id"]]
    assert len(prints) == 2
    assert any(p.get("is_reprint") for p in prints)


def test_failed_generation_no_print_event(repo, tmp_path, monkeypatch) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    order = _order(repo, wid, store, "444")

    def boom(*_a, **_k):
        raise RuntimeError("daraz down")

    monkeypatch.setattr("src.ops._fetch_label_document", boom)
    monkeypatch.setattr("src.ops.client_for_store", lambda s: MagicMock())

    result = print_labels_for_orders(
        wid,
        "u1",
        [order["id"]],
        allow_reprint=False,
        output=tmp_path / "out.pdf",
    )
    assert result["print_status"] == "failed"
    assert result["outcomes"][0]["state"] == "DOCUMENT_FAILED"
    assert result["prints_recorded"] == 0

    prints = repo.list_label_prints_for_orders(wid, [order["id"]])[order["id"]]
    assert prints == []
    assert repo.has_label_print(wid, store["id"], "444") is False


def test_record_label_prints_helper(repo) -> None:
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    order = _order(repo, wid, store, "555")
    rows = record_label_prints(
        wid,
        "u1",
        "job-x",
        [
            {
                "order_id": order["id"],
                "store_id": store["id"],
                "daraz_order_id": "555",
                "order_item_ids": ["item-555"],
                "package_id": "pkg-555",
                "is_reprint": False,
                "fetch_source": "print_awb_pdf",
            }
        ],
    )
    assert len(rows) == 1
    assert rows[0]["fetch_source"] == "print_awb_pdf"


def test_all_rts_remain_visible_after_some_printed(repo) -> None:
    """Printed state is from events — older printed + newer unprinted both list under RTS."""
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(repo, wid)
    morning = _order(repo, wid, store, "900")
    later = _order(repo, wid, store, "901")
    # Morning print event on first order only
    repo.insert_label_print(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "order_id": morning["id"],
            "daraz_order_id": "900",
            "order_item_ids": ["item-900"],
            "printed_at": "2026-09-15T05:02:00+00:00",
        }
    )
    # Later order is "newer" by created_at but has NO event — must stay unprinted
    repo.upsert_daraz_order(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_order_id": "901",
            "order_number": "N-901",
            "status_raw": "ready_to_ship",
            "status_group": "ready_to_ship",
            "statuses": ["ready_to_ship"],
            "created_at_daraz": "2026-09-15T12:00:00+00:00",
        }
    )

    listed = repo.list_orders(
        wid,
        {
            "status_group": "ready_to_ship",
            "print_state": "any",
            "page": 1,
            "page_size": 50,
        },
    )
    ids = {str(o["daraz_order_id"]) for o in listed["items"]}
    assert ids == {"900", "901"}
    by_id = {str(o["daraz_order_id"]): o for o in listed["items"]}
    assert by_id["900"]["has_print"] is True
    assert by_id["901"]["has_print"] is False

    # Timestamp alone must not invent print state for 901
    assert repo.has_label_print(wid, store["id"], "901") is False

    unprinted_only = repo.list_orders(
        wid,
        {
            "status_group": "ready_to_ship",
            "print_state": "unprinted",
            "page": 1,
            "page_size": 50,
        },
    )
    assert {str(o["daraz_order_id"]) for o in unprinted_only["items"]} == {"901"}
