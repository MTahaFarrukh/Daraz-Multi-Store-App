"""Durable create-claim concurrency for bulk product copy."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.product_bulk_copy import _run_job_worker, start_bulk_copy_job
from src.product_copy_mapping import (
    acquire_create_claim,
    get_verified_copy_mapping,
    record_verified_copy_mapping,
    release_create_claim,
)


@pytest.fixture()
def tenancy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "true")
    monkeypatch.setenv("PRODUCT_BULK_COPY_CONCURRENCY", "1")
    monkeypatch.setenv("PRODUCT_COPY_CLAIM_LEASE_SECONDS", "120")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    yield reset_repo_for_tests()


def _ws(repo) -> str:
    return repo.create_workspace_with_owner("u-claim", "Claim W")["workspace"]["id"]


def _store(repo, wid, sid):
    return repo.upsert_store(
        wid,
        {
            "store_id": sid,
            "account": f"{sid}@x.com",
            "seller_id": f"seller-{sid}",
            "display_name": sid,
            "store_name": sid,
            "access_token": f"access-{sid}",
            "refresh_token": f"refresh-{sid}",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )


def _product(repo, wid, store, item_id, title):
    p = repo.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_item_id": item_id,
            "title": title,
            "title_en": title,
            "status_raw": "active",
            "detail_complete": True,
            "primary_category_id": 10001,
            "images_json": [],
            "synced_at": "2026-01-10T00:00:00+00:00",
        }
    )
    repo.upsert_daraz_product_variant(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "product_id": p["id"],
            "daraz_sku_id": f"sku-{item_id}",
            "seller_sku": f"SRC-{item_id}",
            "price": 10.0,
            "quantity": 1,
            "sale_props_json": {"Color": "Red"},
        }
    )
    return p


def _start(wid, src, dst, ids):
    with patch(
        "src.product_bulk_copy.sync_store_products", return_value={"sync_status": "ok"}
    ):
        return start_bulk_copy_job(
            wid,
            source_store_id=src,
            destination_store_id=dst,
            product_item_ids=ids,
            select_all=False,
            refresh_catalog=False,
            spawn_worker=False,
        )


def test_concurrent_jobs_single_createproduct(tenancy_env):
    repo = tenancy_env
    wid = _ws(repo)
    src = _store(repo, wid, "src")
    dst = _store(repo, wid, "dst")
    _product(repo, wid, src, "100", "Shared")
    _product(repo, wid, src, "a1", "A-only")
    _product(repo, wid, src, "b1", "B-only")

    barrier = threading.Barrier(2)
    create_calls: list[str] = []
    lock = threading.Lock()

    def _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        with lock:
            create_calls.append(daraz_item_id)
        # Hold the "remote" create so the peer hits the claim.
        time.sleep(0.35)
        return {
            "destinations": [
                {
                    "status": "VERIFIED",
                    "attempt_state": "VERIFIED",
                    "attempt_id": f"att-{daraz_item_id}",
                    "item_id": f"dest-{daraz_item_id}",
                }
            ]
        }

    job_a = _start(wid, "src", "dst", ["100", "a1"])
    job_b = _start(wid, "src", "dst", ["100", "b1"])
    assert job_a["job"]["id"] != job_b["job"]["id"]

    errors: list[BaseException] = []

    def _run(job_id: str):
        try:
            barrier.wait(timeout=5)
            _run_job_worker(wid, job_id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with patch("src.product_bulk_copy.add_product_from_connected", side_effect=_create):
        t1 = threading.Thread(target=_run, args=(str(job_a["job"]["id"]),))
        t2 = threading.Thread(target=_run, args=(str(job_b["job"]["id"]),))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)
    assert errors == []

    # Shared product created at most once; companions may each create once.
    shared_creates = [c for c in create_calls if c == "100"]
    assert len(shared_creates) == 1

    items_a = repo.list_product_copy_job_items(wid, str(job_a["job"]["id"]), page_size=50)
    items_b = repo.list_product_copy_job_items(wid, str(job_b["job"]["id"]), page_size=50)
    statuses_100 = []
    for batch in (items_a, items_b):
        for row in batch["items"]:
            if row["source_daraz_item_id"] == "100":
                statuses_100.append(row["status"])
    assert "verified" in statuses_100
    assert any(s in {"needs_attention", "needs_reconciliation", "skipped"} for s in statuses_100)
    assert get_verified_copy_mapping(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="100",
        destination_store_id=dst["id"],
    )


def test_stale_held_claim_reclaim_when_safe(tenancy_env):
    repo = tenancy_env
    wid = _ws(repo)
    src = _store(repo, wid, "src")
    dst = _store(repo, wid, "dst")
    _product(repo, wid, src, "55", "Stale")

    expired = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    repo.try_acquire_product_copy_claim(
        {
            "workspace_id": wid,
            "source_store_id": src["id"],
            "source_daraz_item_id": "55",
            "destination_store_id": dst["id"],
            "owner_token": "dead-owner",
            "owner_job_id": None,
            "owner_item_id": None,
            "lease_expires_at": expired,
        }
    )
    # Force lease into the past (acquire sets future lease)
    claim = repo.get_product_copy_claim(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="55",
        destination_store_id=dst["id"],
    )
    assert claim
    repo.update_product_copy_claim(
        wid,
        str(claim["id"]),
        owner_token=str(claim["owner_token"]),
        fields={"lease_expires_at": expired, "state": "held"},
    )

    calls: list[str] = []

    def _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        return {
            "destinations": [
                {
                    "status": "VERIFIED",
                    "attempt_state": "VERIFIED",
                    "attempt_id": "a55",
                    "item_id": "d55",
                }
            ]
        }

    started = _start(wid, "src", "dst", ["55"])
    with patch("src.product_bulk_copy.add_product_from_connected", side_effect=_create):
        _run_job_worker(wid, str(started["job"]["id"]))
    assert calls == ["55"]
    items = repo.list_product_copy_job_items(wid, str(started["job"]["id"]), page_size=10)
    assert items["items"][0]["status"] == "verified"


def test_uncertain_claim_blocks_create(tenancy_env):
    repo = tenancy_env
    wid = _ws(repo)
    src = _store(repo, wid, "src")
    dst = _store(repo, wid, "dst")
    _product(repo, wid, src, "66", "Unc")

    acquired = acquire_create_claim(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="66",
        destination_store_id=dst["id"],
    )
    assert acquired["acquired"] is True
    from src.product_copy_mapping import mark_create_claim_uncertain

    mark_create_claim_uncertain(
        wid,
        claim_id=str(acquired["claim"]["id"]),
        owner_token=str(acquired["owner_token"]),
        attempt_id="att-66",
    )

    calls: list[str] = []

    def _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        return {"destinations": [{"status": "VERIFIED", "item_id": "x"}]}

    started = _start(wid, "src", "dst", ["66"])
    with patch("src.product_bulk_copy.add_product_from_connected", side_effect=_create):
        _run_job_worker(wid, str(started["job"]["id"]))
    assert calls == []
    items = repo.list_product_copy_job_items(wid, str(started["job"]["id"]), page_size=10)
    assert items["items"][0]["status"] == "needs_reconciliation"
    assert "uncertain" in str(items["items"][0].get("reason") or "")


def test_verified_releases_claim_for_future_skip(tenancy_env):
    repo = tenancy_env
    wid = _ws(repo)
    src = _store(repo, wid, "src")
    dst = _store(repo, wid, "dst")
    _product(repo, wid, src, "77", "Ok")
    _product(repo, wid, src, "78", "Extra")

    def _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        return {
            "destinations": [
                {
                    "status": "VERIFIED",
                    "attempt_state": "VERIFIED",
                    "attempt_id": f"a-{daraz_item_id}",
                    "item_id": f"d-{daraz_item_id}",
                }
            ]
        }

    first = _start(wid, "src", "dst", ["77"])
    with patch("src.product_bulk_copy.add_product_from_connected", side_effect=_create):
        _run_job_worker(wid, str(first["job"]["id"]))

    claim = repo.get_product_copy_claim(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="77",
        destination_store_id=dst["id"],
    )
    assert claim is None or claim.get("state") == "released"

    calls: list[str] = []

    def _track(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        return _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw)

    second = _start(wid, "src", "dst", ["77", "78"])
    with patch("src.product_bulk_copy.add_product_from_connected", side_effect=_track):
        _run_job_worker(wid, str(second["job"]["id"]))
    assert calls == ["78"]


def test_claims_independent_per_destination(tenancy_env):
    repo = tenancy_env
    wid = _ws(repo)
    src = _store(repo, wid, "src")
    dst_b = _store(repo, wid, "dst-b")
    dst_c = _store(repo, wid, "dst-c")
    _product(repo, wid, src, "90", "MultiDest")

    a = acquire_create_claim(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="90",
        destination_store_id=dst_b["id"],
    )
    b = acquire_create_claim(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="90",
        destination_store_id=dst_c["id"],
    )
    assert a["acquired"] and b["acquired"]
    release_create_claim(
        wid, claim_id=str(a["claim"]["id"]), owner_token=str(a["owner_token"])
    )
    release_create_claim(
        wid, claim_id=str(b["claim"]["id"]), owner_token=str(b["owner_token"])
    )


def test_failed_releases_claim_for_retry(tenancy_env):
    repo = tenancy_env
    wid = _ws(repo)
    src = _store(repo, wid, "src")
    dst = _store(repo, wid, "dst")
    _product(repo, wid, src, "11", "Fail")

    def _fail(*a, **k):
        raise RuntimeError("boom")

    first = _start(wid, "src", "dst", ["11"])
    with patch("src.product_bulk_copy.add_product_from_connected", side_effect=_fail):
        _run_job_worker(wid, str(first["job"]["id"]))

    claim = repo.get_product_copy_claim(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="11",
        destination_store_id=dst["id"],
    )
    assert claim is None or claim.get("state") == "released"

    calls: list[str] = []

    def _ok(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        return {
            "destinations": [
                {
                    "status": "VERIFIED",
                    "attempt_state": "VERIFIED",
                    "attempt_id": "retry",
                    "item_id": "d11",
                }
            ]
        }

    # Retire failed fingerprint via different companion... actually same set after failed
    # uses fingerprint retirement in start_bulk_copy_job
    second = _start(wid, "src", "dst", ["11"])
    with patch("src.product_bulk_copy.add_product_from_connected", side_effect=_ok):
        _run_job_worker(wid, str(second["job"]["id"]))
    assert calls == ["11"]
