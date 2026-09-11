"""Phase 1A multi-tenant authz and isolation tests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from src.auth import issue_test_token
from src.db import reset_repo_for_tests
from src.print_job import begin_print_job, complete_print_job, job_pdf_path


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
    monkeypatch.setenv("ALLOW_LEGACY_DATA_IMPORT", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    repo = reset_repo_for_tests()
    return repo


@pytest.fixture()
def client(tenancy_env):
    from src.app import app

    return TestClient(app)


def _auth(user_id: str, email: str = "a@example.com") -> dict[str, str]:
    token = issue_test_token(user_id, email=email)
    return {"Authorization": f"Bearer {token}"}


def _bootstrap(client: TestClient, user_id: str, email: str) -> str:
    headers = _auth(user_id, email)
    res = client.post("/api/bootstrap", headers=headers)
    assert res.status_code == 200, res.text
    wid = res.json()["workspace"]["id"]
    headers["X-Workspace-Id"] = wid
    return wid


def _add_store(repo, workspace_id: str, store_id: str, account: str) -> None:
    repo.upsert_store(
        workspace_id,
        {
            "store_id": store_id,
            "account": account,
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


def test_unauthenticated_api_returns_401(client: TestClient) -> None:
    assert client.get("/api/stores").status_code == 401
    assert client.get("/api/orders").status_code == 401
    assert client.post("/api/refresh-tokens").status_code == 401
    assert client.post("/api/print-labels").status_code == 401


def test_signup_bootstrap_creates_workspace_owner(client: TestClient, tenancy_env) -> None:
    wid = _bootstrap(client, "user-a", "a@example.com")
    memberships = tenancy_env.list_memberships("user-a")
    assert len(memberships) == 1
    assert memberships[0]["workspace_id"] == wid
    assert memberships[0]["role"] == "owner"


def test_user_cannot_view_other_workspace_stores(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    _add_store(tenancy_env, wid_a, "store_a", "a@seller.com")
    _add_store(tenancy_env, wid_b, "store_b", "b@seller.com")

    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    res = client.get("/api/stores", headers=headers_a)
    assert res.status_code == 200
    ids = {s["store_id"] for s in res.json()["stores"]}
    assert ids == {"store_a"}
    assert "store_b" not in ids

    # Crafted request: User A asks for User B workspace
    headers_a["X-Workspace-Id"] = wid_b
    deny = client.get("/api/stores", headers=headers_a)
    assert deny.status_code == 403


def test_user_cannot_rename_other_workspace_store(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    _add_store(tenancy_env, wid_b, "store_b", "b@seller.com")

    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    res = client.patch(
        "/api/stores/store_b",
        headers=headers_a,
        json={"display_name": "Hacked"},
    )
    assert res.status_code == 404


def test_user_cannot_refresh_other_workspace_store(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    _add_store(tenancy_env, wid_b, "store_b", "b@seller.com")

    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    res = client.post(
        "/api/refresh-tokens?stores=store_b&force=true",
        headers=headers_a,
    )
    assert res.status_code == 400
    assert "Unknown store" in res.json()["detail"]


def test_user_cannot_load_orders_from_other_store(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    _add_store(tenancy_env, wid_b, "store_b", "b@seller.com")

    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    res = client.get("/api/orders?stores=store_b", headers=headers_a)
    assert res.status_code == 400
    assert "Unknown store" in res.json()["detail"]


def test_user_cannot_print_with_other_store(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    _add_store(tenancy_env, wid_b, "store_b", "b@seller.com")

    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    res = client.post("/api/print-labels?stores=store_b&wait=true", headers=headers_a)
    assert res.status_code == 400


def test_print_job_isolation_status_and_download(
    client: TestClient, tenancy_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.print_job.OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr("src.config.OUTPUT_DIR", tmp_path / "output")

    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")

    job_id = begin_print_job(workspace_id=wid_a, user_id="user-a")
    pdf_path = job_pdf_path(wid_a, job_id)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    pdf_path.write_bytes(buf.getvalue())
    complete_print_job(
        job_id,
        {
            "pages": 1,
            "download_url": f"/api/print-labels/{job_id}/download",
            "label_details": [],
        },
    )

    headers_b = _auth("user-b", "b@example.com")
    headers_b["X-Workspace-Id"] = wid_b
    assert client.get(f"/api/print-labels/{job_id}/status", headers=headers_b).status_code == 403
    assert client.get(f"/api/print-labels/{job_id}/download", headers=headers_b).status_code == 403

    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    status = client.get(f"/api/print-labels/{job_id}/status", headers=headers_a)
    assert status.status_code == 200
    assert status.json()["status"] == "done"
    download = client.get(f"/api/print-labels/{job_id}/download", headers=headers_a)
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")


def test_oauth_state_binds_workspace(tenancy_env, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.auth.oauth_state import build_oauth_state, parse_oauth_state

    wid = tenancy_env.create_workspace_with_owner("user-a", "A")["workspace"]["id"]
    state = build_oauth_state(workspace_id=wid, user_id="user-a")
    parsed = parse_oauth_state(state)
    assert parsed["workspace_id"] == wid
    assert parsed["user_id"] == "user-a"


def test_oauth_callback_rejects_foreign_membership(
    client: TestClient, tenancy_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.auth.oauth_state import build_oauth_state

    wid_b = tenancy_env.create_workspace_with_owner("user-b", "B")["workspace"]["id"]
    # State claims user-a for workspace B (user-a is not a member)
    state = build_oauth_state(workspace_id=wid_b, user_id="user-a")
    res = client.get(f"/oauth/callback?code=fake&state={state}")
    assert res.status_code == 403


def test_store_groups_are_workspace_scoped(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    _add_store(tenancy_env, wid_a, "store_a", "a@seller.com")

    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    created = client.post(
        "/api/store-groups",
        headers=headers_a,
        json={"name": "Vendor Ali", "store_ids": ["store_a"]},
    )
    assert created.status_code == 200
    group_id = created.json()["group"]["id"]

    headers_b = _auth("user-b", "b@example.com")
    headers_b["X-Workspace-Id"] = wid_b
    listed = client.get("/api/store-groups", headers=headers_b)
    assert listed.status_code == 200
    assert listed.json()["groups"] == []

    deleted = client.delete(f"/api/store-groups/{group_id}", headers=headers_b)
    assert deleted.status_code == 404


def test_legacy_migration_idempotent(tenancy_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.legacy_import import import_legacy_into_workspace
    from src.token_store import upsert_store

    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / ".token_key"
    key_path.write_text(key, encoding="utf-8")
    tokens_path = tmp_path / "tokens.json"
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKENS_PATH", tokens_path)
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.legacy_import.TOKENS_PATH", tokens_path)
    monkeypatch.setattr("src.legacy_import.DATA_DIR", tmp_path)
    monkeypatch.setattr("src.legacy_import.use_database", lambda: False)
    monkeypatch.setattr("src.token_store.get_env", lambda name, default="": "")

    upsert_store(
        {
            "store_id": "legacy_store",
            "account": "legacy@seller.com",
            "seller_id": "99",
            "display_name": "Legacy Shop",
            "access_token": "legacy-access",
            "refresh_token": "legacy-refresh",
            "expires_in": 1000,
            "refresh_expires_in": 2000,
            "country": "pk",
        },
        path=tokens_path,
    )

    wid = tenancy_env.create_workspace_with_owner("owner-1", "Owner")["workspace"]["id"]
    first = import_legacy_into_workspace(wid)
    second = import_legacy_into_workspace(wid)
    assert first["imported_count"] == 1
    assert second["imported_count"] == 1
    stores = tenancy_env.list_stores(wid)
    assert len(stores) == 1
    assert stores[0]["store_id"] == "legacy_store"
    assert stores[0]["display_name"] == "Legacy Shop"
    assert stores[0]["access_token"] == "legacy-access"


def test_print_jobs_list_is_workspace_scoped(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    job_id = begin_print_job(workspace_id=wid_a, user_id="user-a")
    complete_print_job(job_id, {"pages": 2, "labels": 2})

    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    listed = client.get("/api/print-jobs", headers=headers_a)
    assert listed.status_code == 200
    ids = {j["id"] for j in listed.json()["jobs"]}
    assert job_id in ids

    headers_b = _auth("user-b", "b@example.com")
    headers_b["X-Workspace-Id"] = wid_b
    other = client.get("/api/print-jobs", headers=headers_b)
    assert other.status_code == 200
    assert other.json()["jobs"] == []


def test_legacy_download_endpoints_gone(client: TestClient) -> None:
    assert client.get("/api/download/combined-labels").status_code == 410


def test_empty_store_selection_rejected(client: TestClient, tenancy_env) -> None:
    wid = _bootstrap(client, "user-a", "a@example.com")
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    res = client.get("/api/orders?stores=", headers=headers)
    assert res.status_code == 400
    assert "No stores selected" in res.json()["detail"]
    res2 = client.post("/api/print-labels?stores=", headers=headers)
    assert res2.status_code == 400


def test_unknown_api_path_is_not_spa_html(client: TestClient) -> None:
    res = client.get("/api/does-not-exist-phase1b")
    assert res.status_code == 404
    ct = res.headers.get("content-type", "")
    assert "application/json" in ct
    assert "text/html" not in ct


def test_public_config_has_no_secrets(client: TestClient) -> None:
    res = client.get("/api/public-config")
    assert res.status_code == 200
    body = res.json()
    assert "supabase_url" in body
    assert "supabase_publishable_key" in body
    assert "supabase_anon_key" not in body
    assert "secret" not in body
    assert "jwt" not in str(body).lower()
    assert body["supabase_publishable_key"] == "sb_publishable_test_key"


def test_invalid_rename_rejected(client: TestClient, tenancy_env) -> None:
    wid = _bootstrap(client, "user-a", "a@example.com")
    _add_store(tenancy_env, wid, "store_a", "a@seller.com")
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    blank = client.patch(
        "/api/stores/store_a",
        headers=headers,
        json={"display_name": "   "},
    )
    assert blank.status_code == 422
    ok = client.patch(
        "/api/stores/store_a",
        headers=headers,
        json={"display_name": "  Main Desk  "},
    )
    assert ok.status_code == 200
    store = ok.json()["store"]
    assert store["display_name"] == "Main Desk"
    assert "access_token" not in store
    assert store["connection_status"] in {"connected", "needs_reconnection"}


def test_duplicate_oauth_upsert_same_workspace(tenancy_env) -> None:
    wid = tenancy_env.create_workspace_with_owner("user-a", "A")["workspace"]["id"]
    first = tenancy_env.upsert_store(
        wid,
        {
            "store_id": "vendor_shop_com",
            "account": "vendor@shop.com",
            "seller_id": "seller-99",
            "display_name": "Custom Name",
            "store_name": "Daraz Shop",
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
            "country": "pk",
        },
    )
    second = tenancy_env.upsert_store(
        wid,
        {
            "store_id": "other_slug",
            "account": "vendor@shop.com",
            "seller_id": "seller-99",
            "display_name": "From OAuth",
            "store_name": "Daraz Shop",
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 7200,
            "country": "pk",
        },
    )
    stores = tenancy_env.list_stores(wid)
    assert len(stores) == 1
    assert second["store_id"] == first["store_id"]
    assert stores[0]["display_name"] == "Custom Name"
    assert stores[0]["access_token"] == "access-2"


def test_same_seller_allowed_in_different_workspaces(tenancy_env) -> None:
    wid_a = tenancy_env.create_workspace_with_owner("user-a", "A")["workspace"]["id"]
    wid_b = tenancy_env.create_workspace_with_owner("user-b", "B")["workspace"]["id"]
    payload = {
        "store_id": "shared_seller",
        "account": "same@seller.com",
        "seller_id": "seller-shared",
        "display_name": "Shop",
        "store_name": "Shop",
        "access_token": "a",
        "refresh_token": "r",
        "expires_in": 3600,
        "country": "pk",
    }
    tenancy_env.upsert_store(wid_a, dict(payload))
    tenancy_env.upsert_store(wid_b, dict(payload, access_token="b"))
    assert len(tenancy_env.list_stores(wid_a)) == 1
    assert len(tenancy_env.list_stores(wid_b)) == 1
    assert tenancy_env.list_stores(wid_a)[0]["access_token"] == "a"
    assert tenancy_env.list_stores(wid_b)[0]["access_token"] == "b"


def test_group_cannot_include_foreign_store(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    _add_store(tenancy_env, wid_a, "store_a", "a@seller.com")
    _add_store(tenancy_env, wid_b, "store_b", "b@seller.com")
    headers_b = _auth("user-b", "b@example.com")
    headers_b["X-Workspace-Id"] = wid_b
    res = client.post(
        "/api/store-groups",
        headers=headers_b,
        json={"name": "Steal", "store_ids": ["store_a"]},
    )
    assert res.status_code == 400


def test_empty_group_members_do_not_select_all(client: TestClient, tenancy_env) -> None:
    wid = _bootstrap(client, "user-a", "a@example.com")
    _add_store(tenancy_env, wid, "store_a", "a@seller.com")
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    created = client.post(
        "/api/store-groups",
        headers=headers,
        json={"name": "Emptyish", "store_ids": ["store_a"]},
    )
    assert created.status_code == 200
    gid = created.json()["group"]["id"]
    updated = client.patch(
        f"/api/store-groups/{gid}",
        headers=headers,
        json={"store_ids": []},
    )
    assert updated.status_code == 200
    assert updated.json()["group"]["store_ids"] == []
    orders = client.get("/api/orders?stores=", headers=headers)
    assert orders.status_code == 400


def test_store_list_sanitized_and_has_health(client: TestClient, tenancy_env) -> None:
    wid = _bootstrap(client, "user-a", "a@example.com")
    _add_store(tenancy_env, wid, "store_a", "a@seller.com")
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    res = client.get("/api/stores", headers=headers)
    assert res.status_code == 200
    store = res.json()["stores"][0]
    assert store["store_id"] == "store_a"
    assert "access_token" not in store
    assert "refresh_token" not in store
    assert store["connection_status"] in {"connected", "needs_reconnection"}
    assert "needs_attention" in store
    assert store.get("id")
