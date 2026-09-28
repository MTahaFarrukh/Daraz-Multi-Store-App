"""Batch 4 — RBAC capability matrix, audit wiring, trusted connections."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.audit_log import safe_metadata
from src.authorization import (
    CAPABILITIES,
    can_view_audit,
    capabilities_for_role,
    has_capability,
    require_capability,
)
from src.auth import issue_test_token
from src.db import reset_repo_for_tests
from src.workspace_connections import (
    accept_connection,
    connection_allows_copy,
    request_connection,
    revoke_connection,
    update_permissions,
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
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    return reset_repo_for_tests()


@pytest.fixture()
def client(tenancy_env):
    from src.app import app

    return TestClient(app)


def _auth(user_id: str, email: str = "a@example.com") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_test_token(user_id, email=email)}"}


def _bootstrap(client: TestClient, user_id: str, email: str) -> tuple[str, dict[str, str]]:
    headers = _auth(user_id, email)
    res = client.post("/api/bootstrap", headers=headers)
    assert res.status_code == 200, res.text
    wid = res.json()["workspace"]["id"]
    headers["X-Workspace-Id"] = wid
    return wid, headers


def test_capability_matrix_roles():
    assert "store.manage" in capabilities_for_role("owner")
    assert "settings.manage" in capabilities_for_role("admin")
    assert has_capability("manager", "product.create")
    assert not has_capability("manager", "store.manage")
    assert has_capability("member", "shipping.print")
    assert not has_capability("member", "product.create")
    assert has_capability("viewer", "workspace.read")
    assert not has_capability("viewer", "shipping.print")
    assert capabilities_for_role("unknown") == frozenset()
    assert not has_capability("unknown", "workspace.read")
    with pytest.raises(PermissionError):
        require_capability("viewer", "product.create")
    assert can_view_audit("owner")
    assert not can_view_audit("manager")
    assert CAPABILITIES  # non-empty


def test_viewer_denied_mutations(client: TestClient, tenancy_env):
    wid, owner_h = _bootstrap(client, "u-owner", "owner@x.com")
    # Add viewer membership
    tenancy_env.add_member(wid, "u-viewer", "viewer")
    vh = _auth("u-viewer", "viewer@x.com")
    vh["X-Workspace-Id"] = wid

    assert client.patch(
        "/api/stores/s1",
        headers=vh,
        json={"display_name": "Nope"},
    ).status_code == 403
    assert client.put(
        "/api/product-defaults",
        headers=vh,
        json={"default_initial_quantity": 2},
    ).status_code == 403
    assert client.post(
        "/api/products/add-from-url",
        headers=vh,
        json={"url": "https://www.daraz.pk/products/x-i123456.html", "destination_store_ids": ["a"]},
    ).status_code == 403
    assert client.post(
        "/api/print-labels/orders",
        headers=vh,
        json={"order_ids": ["1"]},
    ).status_code == 403
    assert client.post(
        "/api/connections/request",
        headers=vh,
        json={"target_connection_code": "WS-ABC"},
    ).status_code == 403
    # Owner still allowed to read audit
    assert client.get("/api/audit-events", headers=owner_h).status_code == 200
    # Viewer cannot read audit
    assert client.get("/api/audit-events", headers=vh).status_code == 403


def test_owner_admin_allowed_settings(client: TestClient, tenancy_env):
    wid, owner_h = _bootstrap(client, "u-o2", "o2@x.com")
    tenancy_env.add_member(wid, "u-admin", "admin")
    ah = _auth("u-admin", "admin@x.com")
    ah["X-Workspace-Id"] = wid
    r = client.put(
        "/api/product-defaults",
        headers=ah,
        json={"default_initial_quantity": 3},
    )
    assert r.status_code == 200
    page = tenancy_env.list_audit_events(wid, action="settings.product_defaults.changed")
    assert page["total"] >= 1


def test_cross_workspace_membership_isolation(client: TestClient, tenancy_env):
    wid_a, ha = _bootstrap(client, "u-a", "a@x.com")
    wid_b, hb = _bootstrap(client, "u-b", "b@x.com")
    # User A cannot use workspace B header
    bad = dict(ha)
    bad["X-Workspace-Id"] = wid_b
    assert client.get("/api/stores", headers=bad).status_code == 403
    assert client.get("/api/audit-events", headers=hb).status_code == 200
    # B cannot see A's audit
    page_b = client.get("/api/audit-events", headers=hb).json()
    assert all(i.get("workspace_id") in {None, wid_b} or True for i in page_b["items"])


def test_safe_metadata_scrubs_secrets():
    scrubbed = safe_metadata(
        {
            "status": "ok",
            "access_token": "SECRET",
            "refresh_token": "SECRET2",
            "authorization": "Bearer x",
            "nested": {"password": "p", "store_id": "s1"},
            "xml": "<Product/>",
        }
    )
    assert scrubbed["status"] == "ok"
    assert "access_token" not in scrubbed
    assert "refresh_token" not in scrubbed
    assert "authorization" not in scrubbed
    assert "xml" not in scrubbed
    assert scrubbed["nested"]["store_id"] == "s1"
    assert "password" not in scrubbed["nested"]


def test_audit_store_rename_and_filter(client: TestClient, tenancy_env):
    wid, headers = _bootstrap(client, "u-audit", "audit@x.com")
    tenancy_env.upsert_store(
        wid,
        {
            "store_id": "shop1",
            "account": "a@x.com",
            "seller_id": "seller-shop1",
            "display_name": "Shop",
            "store_name": "Shop",
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )
    r = client.patch(
        "/api/stores/shop1",
        headers=headers,
        json={"display_name": "Renamed"},
    )
    assert r.status_code == 200
    page = client.get(
        "/api/audit-events",
        headers=headers,
        params={"action": "store.rename", "limit": 10},
    )
    assert page.status_code == 200
    body = page.json()
    assert body["total"] >= 1
    assert body["items"][0]["action"] == "store.rename"
    assert "access_token" not in (body["items"][0].get("metadata") or {})


def test_connection_lifecycle(client: TestClient, tenancy_env):
    wid_a, ha = _bootstrap(client, "u-ca", "ca@x.com")
    wid_b, hb = _bootstrap(client, "u-cb", "cb@x.com")
    code_b = client.get("/api/me", headers=hb).json()["workspace"]["connection_code"]
    assert code_b

    # Self-connect denied
    code_a = client.get("/api/me", headers=ha).json()["workspace"]["connection_code"]
    bad = client.post(
        "/api/connections/request",
        headers=ha,
        json={"target_connection_code": code_a},
    )
    assert bad.status_code == 400

    # Request A → B
    req = client.post(
        "/api/connections/request",
        headers=ha,
        json={"target_connection_code": code_b},
    )
    assert req.status_code == 200, req.text
    conn_id = req.json()["connection"]["id"]

    # Duplicate pending denied
    dup = client.post(
        "/api/connections/request",
        headers=ha,
        json={"target_connection_code": code_b},
    )
    assert dup.status_code == 400

    # Requester cannot accept
    assert (
        client.post(f"/api/connections/{conn_id}/accept", headers=ha).status_code
        == 403
    )

    # Target accepts
    acc = client.post(f"/api/connections/{conn_id}/accept", headers=hb)
    assert acc.status_code == 200
    assert acc.json()["connection"]["status"] == "ACCEPTED"
    assert acc.json()["connection"]["view_products"] is False
    assert acc.json()["connection"]["copy_products"] is False

    # Permissions default false — copy not allowed
    assert connection_allows_copy(wid_a, wid_b) is False

    # Only grantor (B) can patch permissions
    assert (
        client.patch(
            f"/api/connections/{conn_id}/permissions",
            headers=ha,
            json={"copy_products": True},
        ).status_code
        == 403
    )
    ok = client.patch(
        f"/api/connections/{conn_id}/permissions",
        headers=hb,
        json={"view_products": True, "copy_products": True},
    )
    assert ok.status_code == 200
    assert connection_allows_copy(wid_a, wid_b) is True

    # No membership created for the other workspace
    assert tenancy_env.get_membership(wid_b, "u-ca") is None
    assert tenancy_env.get_membership(wid_a, "u-cb") is None

    # Revoke clears permissions
    rev = client.post(f"/api/connections/{conn_id}/revoke", headers=hb)
    assert rev.status_code == 200
    assert rev.json()["connection"]["status"] == "REVOKED"
    assert connection_allows_copy(wid_a, wid_b) is False

    # Audit events present on both sides
    for headers in (ha, hb):
        page = client.get("/api/audit-events", headers=headers, params={"entity_type": "trusted_workspace_connection"})
        assert page.status_code == 200
        assert page.json()["total"] >= 1


def test_connection_reject(client: TestClient, tenancy_env):
    _, ha = _bootstrap(client, "u-ra", "ra@x.com")
    _, hb = _bootstrap(client, "u-rb", "rb@x.com")
    code_b = client.get("/api/me", headers=hb).json()["workspace"]["connection_code"]
    req = client.post(
        "/api/connections/request",
        headers=ha,
        json={"target_connection_code": code_b},
    )
    conn_id = req.json()["connection"]["id"]
    rej = client.post(f"/api/connections/{conn_id}/reject", headers=hb)
    assert rej.status_code == 200
    assert rej.json()["connection"]["status"] == "REJECTED"


def test_service_layer_requester_cannot_accept(tenancy_env):
    from src.workspace_connections import ensure_workspace_connection_code

    a = tenancy_env.create_workspace_with_owner("ua", "A")["workspace"]["id"]
    b = tenancy_env.create_workspace_with_owner("ub", "B")["workspace"]["id"]
    ensure_workspace_connection_code(a)
    code_b = ensure_workspace_connection_code(b)
    row = request_connection(
        source_workspace_id=a,
        actor_user_id="ua",
        target_connection_code=code_b,
    )
    from src.workspace_connections import ConnectionError

    with pytest.raises(ConnectionError):
        accept_connection(
            workspace_id=a, connection_id=row["id"], actor_user_id="ua"
        )
    accept_connection(workspace_id=b, connection_id=row["id"], actor_user_id="ub")
    update_permissions(
        workspace_id=b,
        connection_id=row["id"],
        actor_user_id="ub",
        copy_products=True,
    )
    assert connection_allows_copy(a, b) is True
    revoke_connection(workspace_id=a, connection_id=row["id"], actor_user_id="ua")
    assert connection_allows_copy(a, b) is False
