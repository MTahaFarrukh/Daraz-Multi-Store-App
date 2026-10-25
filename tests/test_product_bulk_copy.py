"""Bulk store-to-store product copy — catalog, job, duplicate safety."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.auth import issue_test_token
from src.db import reset_repo_for_tests
from src.product_bulk_copy import (
    discover_source_catalog,
    human_item_status,
    start_bulk_copy_job,
)
from src.product_clone import build_connected_clone_draft


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


def _bootstrap(repo, user_id="u-bulk"):
    from src.app import app

    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {issue_test_token(user_id, email=f'{user_id}@x.com')}"
    }
    boot = client.post("/api/bootstrap", headers=headers)
    assert boot.status_code == 200
    wid = boot.json()["workspace"]["id"]
    headers["X-Workspace-Id"] = wid
    return wid, client, headers


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


def _seed_product(
    repo,
    workspace_id: str,
    store: dict,
    *,
    item_id: str,
    title: str,
    qty: int | None = 5,
    sale_props: dict | None = None,
    price: float = 199.0,
):
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
            "price": price,
            "quantity": qty,
            "sale_props_json": sale_props or {"Pack": "Pack of 2"},
            "synced_at": "2026-01-10T00:00:00+00:00",
        }
    )
    return product


def test_human_item_status_labels():
    assert human_item_status("needs_reconciliation") == "Needs Reconciliation"
    assert human_item_status("verified") == "Verified"


def test_same_store_rejected(tenancy_env):
    repo = tenancy_env
    wid, _, _ = _bootstrap(repo)
    store = _add_store(repo, wid, "s1")
    _seed_product(repo, wid, store, item_id="1", title="A")
    with pytest.raises(ValueError, match="different"):
        start_bulk_copy_job(
            wid,
            source_store_id="s1",
            destination_store_id="s1",
            select_all=True,
            refresh_catalog=False,
        )


def test_catalog_select_all_across_products(tenancy_env):
    repo = tenancy_env
    wid, client, headers = _bootstrap(repo)
    src = _add_store(repo, wid, "src")
    _add_store(repo, wid, "dst")
    for i in range(5):
        _seed_product(repo, wid, src, item_id=str(i + 1), title=f"P{i}")

    with patch("src.product_bulk_copy.sync_store_products", return_value={"sync_status": "ok"}):
        catalog = discover_source_catalog(wid, "src", refresh=True, page=1, page_size=2)
    assert catalog["total"] == 5
    assert catalog["eligible_count"] == 5
    assert len(catalog["eligible_item_ids"]) == 5
    assert len(catalog["items"]) == 2  # page size

    api = client.post(
        "/api/products/bulk-copy/catalog",
        headers=headers,
        json={"source_store_id": "src", "refresh": False, "page": 1, "page_size": 2},
    )
    assert api.status_code == 200
    assert api.json()["eligible_count"] == 5


def test_workspace_isolation_and_authz(tenancy_env):
    repo = tenancy_env
    wid_a, client, headers_a = _bootstrap(repo, "u-a")
    wid_b, client_b, headers_b = _bootstrap(repo, "u-b")
    src = _add_store(repo, wid_a, "src-a")
    _add_store(repo, wid_a, "dst-a")
    _seed_product(repo, wid_a, src, item_id="9", title="Only A")

    with patch("src.product_bulk_copy.sync_store_products", return_value={"sync_status": "ok"}):
        started = start_bulk_copy_job(
            wid_a,
            source_store_id="src-a",
            destination_store_id="dst-a",
            select_all=True,
            refresh_catalog=False,
            spawn_worker=False,
        )
    job_id = started["job"]["id"]

    other = client_b.get(f"/api/products/bulk-copy/jobs/{job_id}", headers=headers_b)
    assert other.status_code == 404

    # Viewer denied
    repo.add_member(wid_a, "u-viewer", "viewer")
    vh = {
        "Authorization": f"Bearer {issue_test_token('u-viewer', email='v@x.com')}",
        "X-Workspace-Id": wid_a,
    }
    denied = client.post(
        "/api/products/bulk-copy/catalog",
        headers=vh,
        json={"source_store_id": "src-a", "refresh": False},
    )
    assert denied.status_code == 403


def test_duplicate_job_submission_prevented(tenancy_env):
    repo = tenancy_env
    wid, _, _ = _bootstrap(repo)
    src = _add_store(repo, wid, "src")
    _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="10", title="One")

    with patch("src.product_bulk_copy.sync_store_products", return_value={"sync_status": "ok"}), patch(
        "src.product_bulk_copy.add_product_from_connected",
        return_value={
            "destinations": [
                {"status": "VERIFIED", "attempt_id": "a1", "item_id": "dest-1"}
            ]
        },
    ):
        first = start_bulk_copy_job(
            wid,
            source_store_id="src",
            destination_store_id="dst",
            select_all=True,
            refresh_catalog=False,
        )
        # Wait briefly for worker or force status
        job = first["job"]
        repo.update_product_copy_job(
            wid, str(job["id"]), {"status": "completed", "verified_count": 1, "completed_items": 1}
        )
        second = start_bulk_copy_job(
            wid,
            source_store_id="src",
            destination_store_id="dst",
            select_all=True,
            refresh_catalog=False,
        )
    assert second["duplicate_prevented"] is True
    assert second["job"]["id"] == first["job"]["id"]


def test_partial_failure_does_not_abort_others(tenancy_env):
    repo = tenancy_env
    wid, _, _ = _bootstrap(repo)
    src = _add_store(repo, wid, "src")
    _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="1", title="Ok")
    _seed_product(repo, wid, src, item_id="2", title="Fail")

    calls: list[str] = []

    def _fake_add(workspace_id, source_store_id, daraz_item_id, destination_store_ids, **kw):
        calls.append(daraz_item_id)
        if daraz_item_id == "2":
            raise RuntimeError("boom")
        return {
            "destinations": [
                {"status": "VERIFIED", "attempt_id": "ax", "item_id": f"d-{daraz_item_id}"}
            ]
        }

    with patch("src.product_bulk_copy.sync_store_products", return_value={"sync_status": "ok"}), patch(
        "src.product_bulk_copy.add_product_from_connected", side_effect=_fake_add
    ):
        started = start_bulk_copy_job(
            wid,
            source_store_id="src",
            destination_store_id="dst",
            select_all=True,
            refresh_catalog=False,
            spawn_worker=False,
        )
        job_id = str(started["job"]["id"])
        from src.product_bulk_copy import _run_job_worker

        _run_job_worker(wid, job_id)

    job = repo.get_product_copy_job(wid, job_id)
    assert set(calls) == {"1", "2"}
    assert int(job["verified_count"]) == 1
    assert int(job["failed_count"]) == 1
    assert job["status"] in {"partial", "completed", "failed"}


def test_pack_and_known_quantity_preserved_in_clone(tenancy_env):
    repo = tenancy_env
    wid = repo.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(repo, wid, "src")
    dst = _add_store(repo, wid, "dst")
    product = _seed_product(
        repo,
        wid,
        src,
        item_id="77",
        title="Pack Product",
        qty=12,
        sale_props={"Pack": "Pack of 2"},
        price=350.0,
    )
    # Unknown qty variant
    repo.upsert_daraz_product_variant(
        {
            "workspace_id": wid,
            "store_id": src["id"],
            "product_id": product["id"],
            "daraz_sku_id": "sku-77b",
            "seller_sku": "SRC-77B",
            "price": 360.0,
            "quantity": None,
            "sale_props_json": {"Color": "Red"},
        }
    )

    draft = build_connected_clone_draft(
        wid, source_product_id=product["id"], destination_store_id="dst"
    )
    variants = (draft.get("draft") or {}).get("variants") or []
    assert len(variants) == 2
    pack = next(v for v in variants if (v.get("sale_props") or {}).get("Pack") == "Pack of 2")
    color = next(v for v in variants if (v.get("sale_props") or {}).get("Color") == "Red")
    assert pack["quantity"] == 12
    assert pack["price"] == 350.0
    # Unknown source qty uses workspace default (1), never forced zero
    assert color["quantity"] == 1
    assert color.get("source_quantity") is None
    # Destination seller SKUs are generated — not source SKU ids
    assert pack["seller_sku"] != "SRC-77"
    assert pack.get("source_daraz_sku_id") == "sku-77"


def test_api_start_and_list_items(tenancy_env):
    import time

    repo = tenancy_env
    wid, client, headers = _bootstrap(repo, "u-api")
    src = _add_store(repo, wid, "src")
    _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="55", title="API")

    with patch("src.product_bulk_copy.sync_store_products", return_value={"sync_status": "ok"}), patch(
        "src.product_bulk_copy.add_product_from_connected",
        return_value={
            "destinations": [
                {
                    "status": "VERIFIED",
                    "attempt_id": "a55",
                    "item_id": "dest-55",
                }
            ]
        },
    ):
        res = client.post(
            "/api/products/bulk-copy/jobs",
            headers=headers,
            json={
                "source_store_id": "src",
                "destination_store_id": "dst",
                "select_all": True,
                "refresh_catalog": False,
            },
        )
        assert res.status_code == 200
        assert res.json().get("product_create_enabled") is True
        job_id = res.json()["job"]["id"]
        # Wait for background worker under the same mock patch.
        for _ in range(80):
            job = repo.get_product_copy_job(wid, job_id)
            if job and job.get("status") not in {"queued", "running"}:
                break
            time.sleep(0.05)
    items = client.get(
        f"/api/products/bulk-copy/jobs/{job_id}/items", headers=headers
    )
    assert items.status_code == 200
    body = items.json()
    assert body["total"] == 1
    assert body["items"][0]["status_label"]


def test_bulk_start_blocked_when_create_disabled(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    repo = tenancy_env
    wid, client, headers = _bootstrap(repo, "u-gate")
    src = _add_store(repo, wid, "src")
    _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="88", title="Blocked")

    with patch("src.product_bulk_copy.sync_store_products", return_value={"sync_status": "ok"}):
        with pytest.raises(ValueError, match="Product creation is disabled"):
            start_bulk_copy_job(
                wid,
                source_store_id="src",
                destination_store_id="dst",
                select_all=True,
                refresh_catalog=False,
                spawn_worker=False,
            )
        res = client.post(
            "/api/products/bulk-copy/jobs",
            headers=headers,
            json={
                "source_store_id": "src",
                "destination_store_id": "dst",
                "select_all": True,
                "refresh_catalog": False,
            },
        )
    assert res.status_code == 400
    assert "Product creation is disabled" in str(res.json().get("detail") or "")
    assert not any(
        str(j.get("workspace_id")) == str(wid)
        for j in getattr(repo, "product_copy_jobs", {}).values()
    )


def test_bulk_retry_blocked_when_create_disabled(tenancy_env, monkeypatch):
    """Retry Failed must require ALLOW_PRODUCT_CREATE (probe alone insufficient)."""
    from src.product_bulk_copy import retry_failed_bulk_copy_items

    repo = tenancy_env
    wid, client, headers = _bootstrap(repo, "u-retry-gate")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "true")
    with pytest.raises(ValueError, match="Product creation is disabled"):
        retry_failed_bulk_copy_items(wid, "00000000-0000-0000-0000-000000000001")
    res = client.post(
        "/api/products/bulk-copy/jobs/00000000-0000-0000-0000-000000000001/retry-failed",
        headers=headers,
    )
    assert res.status_code == 400
    assert "Product creation is disabled" in str(res.json().get("detail") or "")


def test_bulk_start_reuses_existing_job_when_create_disabled(tenancy_env, monkeypatch):
    """Duplicate fingerprint short-circuit still works before the create gate."""
    repo = tenancy_env
    wid, _, _ = _bootstrap(repo, "u-reuse")
    src = _add_store(repo, wid, "src")
    _add_store(repo, wid, "dst")
    _seed_product(repo, wid, src, item_id="99", title="Reuse")

    with patch("src.product_bulk_copy.sync_store_products", return_value={"sync_status": "ok"}), patch(
        "src.product_bulk_copy.add_product_from_connected",
        return_value={
            "destinations": [
                {"status": "VERIFIED", "attempt_id": "a99", "item_id": "d99"}
            ]
        },
    ):
        first = start_bulk_copy_job(
            wid,
            source_store_id="src",
            destination_store_id="dst",
            select_all=True,
            refresh_catalog=False,
            spawn_worker=False,
        )
        repo.update_product_copy_job(
            wid,
            str(first["job"]["id"]),
            {"status": "completed", "verified_count": 1, "completed_items": 1},
        )

    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    second = start_bulk_copy_job(
        wid,
        source_store_id="src",
        destination_store_id="dst",
        select_all=True,
        refresh_catalog=False,
        spawn_worker=False,
    )
    assert second["duplicate_prevented"] is True
    assert second["job"]["id"] == first["job"]["id"]
