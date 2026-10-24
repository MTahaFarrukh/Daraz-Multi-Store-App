"""Durable per-product source→destination dedupe for bulk copy."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.product_bulk_copy import _run_job_worker, start_bulk_copy_job
from src.product_copy_mapping import (
    get_verified_copy_mapping,
    record_verified_copy_mapping,
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
    monkeypatch.setenv("PRODUCT_BULK_COPY_CONCURRENCY", "2")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    yield reset_repo_for_tests()


def _add_store(repo, workspace_id: str, store_id: str) -> dict[str, Any]:
    return repo.upsert_store(
        workspace_id,
        {
            "store_id": store_id,
            "account": f"{store_id}@x.com",
            "seller_id": f"seller-{store_id}",
            "display_name": store_id,
            "store_name": store_id,
            "access_token": f"access-{store_id}",
            "refresh_token": f"refresh-{store_id}",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )


def _seed_product(repo, workspace_id: str, store: dict, *, item_id: str, title: str):
    product = repo.upsert_daraz_product(
        {
            "workspace_id": workspace_id,
            "store_id": store["id"],
            "daraz_item_id": item_id,
            "title": title,
            "title_en": title,
            "status_raw": "active",
            "detail_complete": True,
            "primary_category_id": 10001,
            "images_json": [{"url": f"https://img.example/{item_id}.jpg"}],
            "synced_at": "2026-01-10T00:00:00+00:00",
        }
    )
    repo.upsert_daraz_product_variant(
        {
            "workspace_id": workspace_id,
            "store_id": store["id"],
            "product_id": product["id"],
            "daraz_sku_id": f"sku-{item_id}",
            "seller_sku": f"SRC-{item_id}",
            "price": 199.0,
            "quantity": 5,
            "sale_props_json": {"Pack": "Pack of 1"},
            "synced_at": "2026-01-10T00:00:00+00:00",
        }
    )
    return product


def _workspace(repo) -> str:
    return repo.create_workspace_with_owner("u-dedupe", "Dedupe W")["workspace"]["id"]


def _run_copy(wid, *, src, dst, item_ids, create_fn):
    from src.db.repo import get_repo

    with patch("src.product_bulk_copy.sync_store_products", return_value={"sync_status": "ok"}), patch(
        "src.product_bulk_copy.add_product_from_connected", side_effect=create_fn
    ):
        started = start_bulk_copy_job(
            wid,
            source_store_id=src,
            destination_store_id=dst,
            product_item_ids=item_ids,
            select_all=False,
            refresh_catalog=False,
            spawn_worker=False,
        )
        job_id = str(started["job"]["id"])
        _run_job_worker(wid, job_id)
        job = get_repo().get_product_copy_job(wid, job_id) or started["job"]
        return {**started, "job": job}


def test_verified_product_skipped_in_new_job_fingerprint(tenancy_env):
    repo = tenancy_env
    wid = _workspace(repo)
    src = _add_store(repo, wid, "src")
    dst = _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="100", title="Alpha")

    calls: list[str] = []

    def _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        return {
            "destinations": [
                {
                    "status": "VERIFIED",
                    "creation_status": "VERIFIED",
                    "attempt_state": "VERIFIED",
                    "attempt_id": f"att-{daraz_item_id}",
                    "item_id": f"dest-{daraz_item_id}",
                }
            ]
        }

    job_a = _run_copy(wid, src="src", dst="dst", item_ids=["100"], create_fn=_create)
    assert set(calls) == {"100"}
    mapping = get_verified_copy_mapping(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="100",
        destination_store_id=dst["id"],
    )
    assert mapping is not None
    assert mapping["destination_item_id"] == "dest-100"

    # Different fingerprint: include an extra product
    _seed_product(repo, wid, src, item_id="101", title="Beta")
    calls.clear()
    job_b = _run_copy(
        wid, src="src", dst="dst", item_ids=["100", "101"], create_fn=_create
    )
    assert job_b["job"]["id"] != job_a["job"]["id"]
    assert calls == ["101"]  # CreateProduct path never for 100

    items = repo.list_product_copy_job_items(wid, str(job_b["job"]["id"]), page_size=100)
    by_src = {r["source_daraz_item_id"]: r for r in items["items"]}
    assert by_src["100"]["status"] == "skipped"
    assert by_src["100"]["reason"] == "already_copied_to_destination"
    assert by_src["100"]["destination_item_id"] == "dest-100"
    assert by_src["101"]["status"] == "verified"
    job = repo.get_product_copy_job(wid, str(job_b["job"]["id"]))
    assert int(job["skipped_count"]) == 1
    assert int(job["verified_count"]) == 1


def test_hundred_skipped_ten_new_enter_create(tenancy_env):
    repo = tenancy_env
    wid = _workspace(repo)
    src = _add_store(repo, wid, "src")
    dst = _add_store(repo, wid, "dst")
    for i in range(1, 111):
        _seed_product(repo, wid, src, item_id=str(i), title=f"P{i}")

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

    _run_copy(
        wid,
        src="src",
        dst="dst",
        item_ids=[str(i) for i in range(1, 101)],
        create_fn=_create,
    )

    create_calls: list[str] = []

    def _create_track(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        create_calls.append(daraz_item_id)
        return _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw)

    job_b = _run_copy(
        wid,
        src="src",
        dst="dst",
        item_ids=[str(i) for i in range(1, 111)],
        create_fn=_create_track,
    )
    assert set(create_calls) == {str(i) for i in range(101, 111)}
    job = repo.get_product_copy_job(wid, str(job_b["job"]["id"]))
    assert int(job["skipped_count"]) == 100
    assert int(job["verified_count"]) == 10


def test_same_source_can_copy_to_different_destination(tenancy_env):
    repo = tenancy_env
    wid = _workspace(repo)
    src = _add_store(repo, wid, "src")
    dst_b = _add_store(repo, wid, "dst-b")
    dst_c = _add_store(repo, wid, "dst-c")
    _seed_product(repo, wid, src, item_id="50", title="Shared")

    calls: list[str] = []

    def _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(destination_store_ids[0])
        return {
            "destinations": [
                {
                    "status": "VERIFIED",
                    "attempt_state": "VERIFIED",
                    "attempt_id": f"a-{destination_store_ids[0]}",
                    "item_id": f"d-{destination_store_ids[0]}",
                }
            ]
        }

    _run_copy(wid, src="src", dst="dst-b", item_ids=["50"], create_fn=_create)
    _run_copy(wid, src="src", dst="dst-c", item_ids=["50"], create_fn=_create)
    assert calls == ["dst-b", "dst-c"]
    assert get_verified_copy_mapping(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="50",
        destination_store_id=dst_b["id"],
    )
    assert get_verified_copy_mapping(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="50",
        destination_store_id=dst_c["id"],
    )


def test_failed_attempt_not_treated_as_copied(tenancy_env):
    repo = tenancy_env
    wid = _workspace(repo)
    src = _add_store(repo, wid, "src")
    dst = _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="9", title="FailOnce")

    def _fail(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        raise RuntimeError("create_failed")

    job_a = _run_copy(wid, src="src", dst="dst", item_ids=["9"], create_fn=_fail)
    assert get_verified_copy_mapping(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="9",
        destination_store_id=dst["id"],
    ) is None
    items = repo.list_product_copy_job_items(wid, str(job_a["job"]["id"]), page_size=10)
    assert items["items"][0]["status"] == "failed"

    calls: list[str] = []

    def _ok(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        return {
            "destinations": [
                {
                    "status": "VERIFIED",
                    "attempt_state": "VERIFIED",
                    "attempt_id": "a9",
                    "item_id": "d9",
                }
            ]
        }

    # New fingerprint via extra product so job can start after failed-only selection reuse path
    _seed_product(repo, wid, src, item_id="8", title="Other")
    _run_copy(wid, src="src", dst="dst", item_ids=["9", "8"], create_fn=_ok)
    assert "9" in calls


def test_uncertain_attempt_blocks_blind_create(tenancy_env):
    repo = tenancy_env
    wid = _workspace(repo)
    src = _add_store(repo, wid, "src")
    dst = _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="77", title="Uncertain")

    repo.create_product_attempt(
        {
            "workspace_id": wid,
            "source_type": "connected_store",
            "source_identity": f"connected:src:77",
            "destination_store_id": dst["id"],
            "request_fingerprint": "fp-uncertain-77",
            "state": "NEEDS_RECONCILIATION",
            "generated_seller_skus": [{"seller_sku": "MTF-X"}],
            "destination_item_id": None,
            "verification_state": "UNVERIFIED",
        }
    )

    calls: list[str] = []

    def _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        return {"destinations": [{"status": "VERIFIED", "item_id": "x"}]}

    job = _run_copy(wid, src="src", dst="dst", item_ids=["77"], create_fn=_create)
    assert calls == []
    items = repo.list_product_copy_job_items(wid, str(job["job"]["id"]), page_size=10)
    assert items["items"][0]["status"] == "needs_reconciliation"
    assert items["items"][0]["reason"] == "product_create_attempt_requires_reconciliation"


def test_reconcile_then_subsequent_job_skips(tenancy_env):
    repo = tenancy_env
    wid = _workspace(repo)
    src = _add_store(repo, wid, "src")
    dst = _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="88", title="ReconcileMe")

    attempt = repo.create_product_attempt(
        {
            "workspace_id": wid,
            "source_type": "connected_store",
            "source_identity": "connected:src:88",
            "destination_store_id": dst["id"],
            "request_fingerprint": "fp-88",
            "state": "NEEDS_RECONCILIATION",
            "generated_seller_skus": [{"seller_sku": "MTF-88"}],
            "destination_item_id": "pending-88",
            "verification_state": "UNVERIFIED",
        }
    )

    with patch(
        "src.product_create_reconcile.reconcile_product_create_attempt",
        return_value={
            "status": "VERIFIED",
            "attempt": {
                **attempt,
                "state": "VERIFIED",
                "destination_item_id": "dest-88",
            },
            "destination_item_id": "dest-88",
            "reason": "matched_seller_skus",
        },
    ):
        # Simulate reconcile writing mapping the way production reconcile does
        repo.update_product_attempt(
            wid, attempt["id"], state="VERIFIED", destination_item_id="dest-88"
        )
        record_verified_copy_mapping(
            wid,
            source_store_id=src["id"],
            source_daraz_item_id="88",
            destination_store_id=dst["id"],
            destination_item_id="dest-88",
            attempt_id=attempt["id"],
        )

    calls: list[str] = []

    def _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        return {"destinations": [{"status": "VERIFIED", "item_id": "nope"}]}

    job = _run_copy(wid, src="src", dst="dst", item_ids=["88"], create_fn=_create)
    assert calls == []
    items = repo.list_product_copy_job_items(wid, str(job["job"]["id"]), page_size=10)
    assert items["items"][0]["status"] == "skipped"


def test_concurrent_mapping_upsert_unique(tenancy_env):
    repo = tenancy_env
    wid = _workspace(repo)
    src = _add_store(repo, wid, "src")
    dst = _add_store(repo, wid, "dst")
    errors: list[BaseException] = []

    def _upsert():
        try:
            record_verified_copy_mapping(
                wid,
                source_store_id=src["id"],
                source_daraz_item_id="concurrent-1",
                destination_store_id=dst["id"],
                destination_item_id="dest-c1",
                attempt_id="a1",
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_upsert) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    matches = [
        m
        for m in repo.product_copy_mappings.values()
        if m.get("source_daraz_item_id") == "concurrent-1"
        and str(m.get("destination_store_id")) == str(dst["id"])
    ]
    assert len(matches) == 1


def test_skipped_persists_across_repo_reread(tenancy_env):
    """Counters/status survive 'refresh' (re-read from durable store)."""
    repo = tenancy_env
    wid = _workspace(repo)
    src = _add_store(repo, wid, "src")
    dst = _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="5", title="Persist")

    def _create(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        return {
            "destinations": [
                {
                    "status": "VERIFIED",
                    "attempt_state": "VERIFIED",
                    "attempt_id": "a5",
                    "item_id": "d5",
                }
            ]
        }

    _run_copy(wid, src="src", dst="dst", item_ids=["5"], create_fn=_create)
    _seed_product(repo, wid, src, item_id="6", title="New")
    job_b = _run_copy(wid, src="src", dst="dst", item_ids=["5", "6"], create_fn=_create)

    # Simulate refresh: re-fetch job/items from repo (no in-memory worker state)
    job_id = str(job_b["job"]["id"])
    job = repo.get_product_copy_job(wid, job_id)
    items = repo.list_product_copy_job_items(wid, job_id, page_size=100)
    assert int(job["skipped_count"]) == 1
    skipped = [i for i in items["items"] if i["status"] == "skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "already_copied_to_destination"


def test_failed_job_fingerprint_can_be_retired_for_retry(tenancy_env):
    repo = tenancy_env
    wid = _workspace(repo)
    src = _add_store(repo, wid, "src")
    _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="1", title="Only")

    def _fail(*a, **k):
        raise RuntimeError("boom")

    first = _run_copy(wid, src="src", dst="dst", item_ids=["1"], create_fn=_fail)
    assert first["job"]["status"] in {"failed", "partial"}

    calls: list[str] = []

    def _ok(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        return {
            "destinations": [
                {
                    "status": "VERIFIED",
                    "attempt_state": "VERIFIED",
                    "attempt_id": "retry-1",
                    "item_id": "dest-1",
                }
            ]
        }

    second = _run_copy(wid, src="src", dst="dst", item_ids=["1"], create_fn=_ok)
    assert second["duplicate_prevented"] is False
    assert second["job"]["id"] != first["job"]["id"]
    assert calls == ["1"]
