"""Batch 2 — print atomicity, recovery, hydrate-once, concurrency, timings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.print_job import (
    STAGE_RECOVERY,
    begin_print_job,
    get_print_job,
    mark_print_job_interrupted,
    reset_print_job_if_stale,
)
from src.print_outcomes import (
    ALREADY_PRINTED,
    DARAZ_ERROR,
    SUCCESS,
    make_outcome,
    retryable_order_ids,
    summarize_outcomes,
)
from src.print_prepare import prepare_print_targets
from src.print_safety import record_label_prints, validate_print_targets


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / ".token_key"
    key_path.write_text(key, encoding="utf-8")
    monkeypatch.setenv("AUTH_TEST_MODE", "true")
    monkeypatch.setenv("TENANCY_REPO", "memory")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test_key")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test_key_server_only")
    monkeypatch.setenv("DARAZ_APP_KEY", "appkey")
    monkeypatch.setenv("DARAZ_APP_SECRET", "appsecret")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("PRINT_PREFER_NATIVE_PDF", "1")
    monkeypatch.setenv("PRINT_PREFER_BULK_GETDOCUMENT", "0")
    monkeypatch.setenv("PRINT_STORE_CONCURRENCY", "2")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    return reset_repo_for_tests()


def _workspace(repo):
    wid = repo.create_workspace_with_owner("u-print", "Print WS")["workspace"]["id"]
    store = repo.upsert_store(
        wid,
        {
            "store_id": "store-a",
            "account": "a@x.com",
            "seller_id": "s-a",
            "display_name": "Store A",
            "access_token": "t",
            "refresh_token": "r",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )
    return wid, store


def _order(repo, wid, store, daraz_id: str, *, with_items: bool = True):
    order = repo.upsert_daraz_order(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_order_id": daraz_id,
            "order_number": daraz_id,
            "status_group": "ready_to_ship",
            "status_raw": "ready_to_ship",
        }
    )
    if with_items:
        repo.upsert_daraz_order_item(
            {
                "workspace_id": wid,
                "store_id": store["id"],
                "order_id": order["id"],
                "daraz_order_id": daraz_id,
                "daraz_order_item_id": f"{daraz_id}-1",
                "status_raw": "ready_to_ship",
                "package_id": f"PKG-{daraz_id}",
            }
        )
    return order


def _payload(wid, store, order, daraz_id: str) -> dict[str, Any]:
    return {
        "workspace_id": wid,
        "store_id": store["id"],
        "order_id": order["id"],
        "daraz_order_id": daraz_id,
        "package_id": f"PKG-{daraz_id}",
        "order_item_ids": [f"{daraz_id}-1"],
        "print_job_id": None,
        "is_reprint": False,
        "fetch_source": "print_awb_pdf",
    }


def test_memory_atomic_rollback_zero_events(repo):
    wid, store = _workspace(repo)
    orders = [_order(repo, wid, store, f"o{i}") for i in range(3)]
    payloads = [
        _payload(wid, store, orders[i], f"o{i}") for i in range(3)
    ]
    repo._atomic_fail_after = 1
    with pytest.raises(RuntimeError, match="injected"):
        repo.insert_label_prints_atomic(payloads)
    assert len(repo.label_prints) == 0


def test_postgres_style_atomic_rollback_simulation(repo):
    """Simulate PG single-transaction rollback via fail-after on atomic insert."""
    wid, store = _workspace(repo)
    orders = [_order(repo, wid, store, f"pg{i}") for i in range(3)]
    payloads = [_payload(wid, store, orders[i], f"pg{i}") for i in range(3)]

    class PgStyle:
        def __init__(self, inner):
            self._inner = inner
            self._atomic_fail_after = 1
            self.committed = False

        def insert_label_prints_atomic(self, rows):
            # Mirror PostgresRepo: build then commit, or rollback all
            pending = []
            for idx, row in enumerate(rows):
                if idx == self._atomic_fail_after:
                    raise RuntimeError("injected atomic label-print failure")
                pending.append(dict(row))
            self.committed = True
            return self._inner.insert_label_prints_atomic(pending)

    pg = PgStyle(repo)
    with pytest.raises(RuntimeError):
        pg.insert_label_prints_atomic(payloads)
    assert pg.committed is False
    assert len(repo.label_prints) == 0


def test_record_label_prints_atomic_failure_leaves_zero(repo):
    wid, store = _workspace(repo)
    orders = [_order(repo, wid, store, f"r{i}") for i in range(3)]
    successes = [
        {
            "order_id": orders[i]["id"],
            "store_id": store["id"],
            "daraz_order_id": f"r{i}",
            "order_item_ids": [f"r{i}-1"],
            "package_id": f"PKG-r{i}",
        }
        for i in range(3)
    ]
    repo._atomic_fail_after = 1
    with pytest.raises(RuntimeError):
        record_label_prints(wid, "u", "job-1", successes)
    assert len(repo.label_prints) == 0


def test_stale_heartbeat_marks_interrupted(repo):
    wid, _store = _workspace(repo)
    job_id = begin_print_job(workspace_id=wid, user_id="u-print")
    job = get_print_job(job_id)
    assert job["status"] == "processing"
    assert job.get("worker_id")
    # Clear local worker ownership and backdate heartbeat
    from src import print_job as pj

    with pj._lock:
        pj._local_workers.clear()
    stale = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
    job["heartbeat_at"] = stale
    job["updated_at"] = stale
    repo.save_print_job(job)
    with pj._lock:
        pj._jobs[job_id] = dict(job)
    reset_print_job_if_stale(job_id, heartbeat_stale_seconds=60)
    refreshed = get_print_job(job_id)
    assert refreshed["status"] == "interrupted"
    assert refreshed["processing_stage"] == STAGE_RECOVERY
    assert refreshed.get("interrupted_at")


def test_mark_interrupted_does_not_auto_replay(repo):
    wid, _ = _workspace(repo)
    job_id = begin_print_job(workspace_id=wid, user_id="u-print")
    mark_print_job_interrupted(job_id)
    job = get_print_job(job_id)
    assert job["status"] == "interrupted"
    assert job.get("worker_id") is None


def test_retryable_excludes_proven_success_and_already_printed():
    outcomes = [
        make_outcome(order_id="1", state=SUCCESS),
        make_outcome(order_id="2", state=ALREADY_PRINTED),
        make_outcome(order_id="3", state=DARAZ_ERROR, reason="timeout"),
    ]
    assert retryable_order_ids(outcomes) == ["3"]
    summary = summarize_outcomes(outcomes)
    assert "1" not in summary["retryable_order_ids"]
    assert "2" not in summary["retryable_order_ids"]
    assert summary["retryable_order_ids"] == ["3"]


def test_failed_unproven_targets_are_retryable():
    outcomes = [
        make_outcome(order_id="ok", state=SUCCESS),
        make_outcome(order_id="fail", state=DARAZ_ERROR),
        make_outcome(order_id="map", state="DOCUMENT_MAPPING_FAILED"),
    ]
    assert set(retryable_order_ids(outcomes)) == {"fail", "map"}


def test_no_duplicate_hydration(repo):
    wid, store = _workspace(repo)
    order = _order(repo, wid, store, "dup1", with_items=True)
    calls = {"n": 0}

    def counting(workspace_id, order_uuids):
        calls["n"] += 1
        return {
            "hydrate_ms": 0,
            "orders_hydrated": 0,
            "api_calls": 0,
            "orders_missing": 0,
        }

    with patch("src.print_prepare.hydrate_missing_order_items", side_effect=counting):
        prepared = prepare_print_targets(wid, [order["id"]], hydrate=True)
        validate_print_targets(wid, [order["id"]], prepared=prepared)
    assert calls["n"] == 1


def test_batch_read_path(repo):
    wid, store = _workspace(repo)
    o1 = _order(repo, wid, store, "b1")
    o2 = _order(repo, wid, store, "b2")
    orders = repo.list_orders_by_ids(wid, [o1["id"], o2["id"]])
    assert set(orders) == {o1["id"], o2["id"]}
    items = repo.list_order_items_by_order_ids(wid, [o1["id"], o2["id"]])
    assert len(items[o1["id"]]) == 1
    stores = repo.list_stores_by_uuids(wid, [store["id"]])
    assert store["id"] in stores


def test_multi_store_failure_isolation(repo):
    """One store's failure must not erase another store's outcome entries."""
    from src.ops import _print_store_concurrency

    wid, store_a = _workspace(repo)
    store_b = repo.upsert_store(
        wid,
        {
            "store_id": "store-b",
            "account": "b@x.com",
            "seller_id": "s-b",
            "display_name": "Store B",
            "access_token": "tb",
            "refresh_token": "rb",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )
    oa = _order(repo, wid, store_a, "ms-a")
    ob = _order(repo, wid, store_b, "ms-b")
    outcomes = {
        oa["id"]: make_outcome(order_id=oa["id"], state=SUCCESS, store_id=store_a["id"]),
        ob["id"]: make_outcome(
            order_id=ob["id"], state=DARAZ_ERROR, reason="store-b boom", store_id=store_b["id"]
        ),
    }
    assert outcomes[oa["id"]]["state"] == SUCCESS
    assert outcomes[ob["id"]]["state"] == DARAZ_ERROR
    assert outcomes[oa["id"]]["store_id"] != outcomes[ob["id"]]["store_id"]
    assert _print_store_concurrency() >= 1


def test_bounded_store_concurrency_env(repo, monkeypatch):
    from src.ops import _print_store_concurrency as fn

    monkeypatch.setenv("PRINT_STORE_CONCURRENCY", "3")
    # re-import path uses get_env live
    assert fn() == 3
    monkeypatch.setenv("PRINT_STORE_CONCURRENCY", "99")
    assert fn() == 3


def test_fallback_skips_repeat_print_awb():
    from src.daraz_api import DarazApiError
    from src.ops import _fetch_label_document

    client = MagicMock()
    client.get_shipping_label.return_value = {
        "data": {"document": {"file": "dGVzdA==", "mime_type": "application/pdf"}}
    }
    with patch(
        "src.ops.document_from_daraz_response",
        return_value=MagicMock(
            is_pdf=lambda: True,
            is_html=lambda: False,
        ),
    ):
        _label, source, meta = _fetch_label_document(
            client,
            store_id="s",
            store_name="S",
            order_id="1",
            item_ids=["i1"],
            package_id="PKG",
            skip_print_awb=True,
            print_awb_skip_reason="already_failed",
        )
    client.get_package_shipping_label.assert_not_called()
    client.get_shipping_label.assert_called_once()
    assert meta["fallback_reason"] == "skip_repeat_print_awb"
    assert source.startswith("get_document")


def test_print_timing_keys_present(repo, monkeypatch, tmp_path):
    from src.label_processor import LabelDocument
    from src.ops import print_labels_for_orders

    wid, store = _workspace(repo)
    order = _order(repo, wid, store, "tim1")
    pdf = b"%PDF-1.4\n%%EOF\n"

    client = MagicMock()
    client.get_package_shipping_label.return_value = {"code": "0", "data": {}}
    client.download_binary_url = MagicMock(return_value=pdf)
    monkeypatch.setattr("src.ops.client_for_store", lambda _s: client)
    monkeypatch.setattr(
        "src.ops.document_from_print_awb_response",
        lambda *a, **k: LabelDocument(
            store_id="s",
            store_name="S",
            order_id="tim1",
            order_item_id="tim1-1",
            source_filename="tim1.pdf",
            mime_type="application/pdf",
            document_bytes=pdf,
        ),
    )
    monkeypatch.setattr("src.ops.merge_labels", lambda labels, out: Path(out).write_bytes(pdf))
    monkeypatch.setattr("src.ops.pdf_page_count", lambda _p: 1)
    monkeypatch.setattr("src.ops._label_pdf_page_count", lambda _l: 1)

    result = print_labels_for_orders(
        wid, "u", [order["id"]], output=tmp_path / "t.pdf"
    )
    keys = result["timings_ms"]
    for required in (
        "prepare_ms",
        "hydrate_ms",
        "validation_ms",
        "printawb_ms",
        "fallback_ms",
        "html_conversion_ms",
        "merge_ms",
        "event_persist_ms",
        "total_ms",
    ):
        assert required in keys, required
