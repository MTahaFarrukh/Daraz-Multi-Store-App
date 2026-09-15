"""Phase 3 unified local orders — repo, API, sync isolation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.auth import issue_test_token
from src.db import reset_repo_for_tests
from src.order_status import map_status_group


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
    return res.json()["workspace"]["id"]


def _add_store(repo, workspace_id: str, store_id: str, account: str) -> dict:
    return repo.upsert_store(
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


def _seed_order(repo, workspace_id: str, store: dict, daraz_order_id: str, **extra):
    payload = {
        "workspace_id": workspace_id,
        "store_id": store["id"],
        "daraz_order_id": daraz_order_id,
        "order_number": extra.get("order_number", f"ON-{daraz_order_id}"),
        "status_raw": extra.get("status_raw", "ready_to_ship"),
        "status_group": extra.get("status_group", "ready_to_ship"),
        "statuses": extra.get("statuses", ["ready_to_ship"]),
        "created_at_daraz": extra.get("created_at_daraz", "2026-09-01T10:00:00+00:00"),
        "customer_first_name": extra.get("customer_first_name", "Ali"),
        "customer_last_name": extra.get("customer_last_name", "Khan"),
        "price": extra.get("price", 100),
        "currency": "PKR",
        "items_count": 1,
    }
    order = repo.upsert_daraz_order(payload)
    item = repo.upsert_daraz_order_item(
        {
            "workspace_id": workspace_id,
            "store_id": store["id"],
            "order_id": order["id"],
            "daraz_order_item_id": extra.get("item_id", f"item-{daraz_order_id}"),
            "daraz_order_id": daraz_order_id,
            "status_raw": "ready_to_ship",
            "package_id": extra.get("package_id", f"pkg-{daraz_order_id}"),
            "name": "Widget",
            "sku": f"SKU-{daraz_order_id}",
            "quantity": 1,
        }
    )
    return order, item


def test_status_mapping_case_insensitive() -> None:
    assert map_status_group("Ready_To_Ship") == "ready_to_ship"
    assert map_status_group(["CANCELLED"]) == "canceled"
    assert map_status_group("packed") == "ready_to_ship"
    assert map_status_group("weird_unknown") == "other"
    assert map_status_group(["shipped", "delivered"]) == "delivered"


def test_upsert_order_idempotent(tenancy_env) -> None:
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "s1", "a@s.com")
    first, _ = _seed_order(tenancy_env, wid, store, "1001", order_number="A")
    second, _ = _seed_order(tenancy_env, wid, store, "1001", order_number="B")
    assert first["id"] == second["id"]
    assert second["order_number"] == "B"
    listed = tenancy_env.list_orders(wid, {"page": 1, "page_size": 50})
    assert listed["total"] == 1


def test_cross_store_same_daraz_order_id_isolated(tenancy_env) -> None:
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    s1 = _add_store(tenancy_env, wid, "s1", "a@s.com")
    s2 = _add_store(tenancy_env, wid, "s2", "b@s.com")
    o1, _ = _seed_order(tenancy_env, wid, s1, "shared-oid")
    o2, _ = _seed_order(tenancy_env, wid, s2, "shared-oid")
    assert o1["id"] != o2["id"]
    assert tenancy_env.list_orders(wid, {})["total"] == 2


def test_workspace_403_on_foreign_order_detail(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    store_b = _add_store(tenancy_env, wid_b, "store_b", "b@seller.com")
    order, _ = _seed_order(tenancy_env, wid_b, store_b, "999")

    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    res = client.get(f"/api/orders/{order['id']}", headers=headers_a)
    assert res.status_code == 404

    headers_a["X-Workspace-Id"] = wid_b
    deny = client.get(f"/api/orders/{order['id']}", headers=headers_a)
    assert deny.status_code == 403


def test_list_orders_pagination(tenancy_env) -> None:
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "s1", "a@s.com")
    for i in range(5):
        _seed_order(
            tenancy_env,
            wid,
            store,
            str(1000 + i),
            created_at_daraz=f"2026-09-0{i + 1}T10:00:00+00:00",
        )
    page1 = tenancy_env.list_orders(wid, {"page": 1, "page_size": 2})
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    page3 = tenancy_env.list_orders(wid, {"page": 3, "page_size": 2})
    assert len(page3["items"]) == 1


def test_empty_stores_param_400(client: TestClient, tenancy_env) -> None:
    wid = _bootstrap(client, "user-a", "a@example.com")
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    res = client.get("/api/orders?stores=", headers=headers)
    assert res.status_code == 400
    assert "No stores selected" in res.json()["detail"]


def test_get_orders_does_not_call_daraz(
    client: TestClient, tenancy_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    wid = _bootstrap(client, "user-a", "a@example.com")
    store = _add_store(tenancy_env, wid, "store_a", "a@seller.com")
    _seed_order(tenancy_env, wid, store, "555")

    called = {"fetch": False}

    def boom(*_a, **_k):
        called["fetch"] = True
        raise AssertionError("fetch_orders should not be called")

    monkeypatch.setattr("src.app.fetch_orders", boom)
    monkeypatch.setattr("src.ops.fetch_orders", boom)

    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    res = client.get("/api/orders", headers=headers)
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert called["fetch"] is False


def test_unknown_store_filter_rejected(client: TestClient, tenancy_env) -> None:
    wid_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b = _bootstrap(client, "user-b", "b@example.com")
    _add_store(tenancy_env, wid_b, "store_b", "b@seller.com")
    headers_a = _auth("user-a", "a@example.com")
    headers_a["X-Workspace-Id"] = wid_a
    res = client.get("/api/orders?stores=store_b", headers=headers_a)
    assert res.status_code == 400
    assert "Unknown store" in res.json()["detail"]


def test_status_counts_endpoint(client: TestClient, tenancy_env) -> None:
    wid = _bootstrap(client, "user-a", "a@example.com")
    store = _add_store(tenancy_env, wid, "store_a", "a@seller.com")
    _seed_order(tenancy_env, wid, store, "1", status_group="ready_to_ship")
    _seed_order(
        tenancy_env,
        wid,
        store,
        "2",
        status_group="canceled",
        status_raw="canceled",
        item_id="item-2",
    )
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    res = client.get("/api/orders/status-counts", headers=headers)
    assert res.status_code == 200
    counts = res.json()["counts"]
    assert counts.get("ready_to_ship") == 1
    assert counts.get("canceled") == 1


def test_sync_default_window_uses_created_after_30d(
    tenancy_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default sync must use created_after (~30d), not update_after (~7d).

    Stale RTS that were not updated recently were missing for whole stores.
    """
    from src.order_sync import sync_store_orders

    ws = tenancy_env.create_workspace_with_owner("user-sync", "Sync WS")
    wid = ws["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "store_sync", "sync@seller.com")
    captured: dict = {}

    mock_client = MagicMock()

    def _get_orders(**kwargs):
        captured.update(kwargs)
        return {"data": {"orders": []}}

    mock_client.get_orders.side_effect = _get_orders
    mock_client.timeout = 60.0
    monkeypatch.setattr("src.order_sync.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.order_sync.access_token_expires_soon", lambda *_a, **_k: False
    )

    result = sync_store_orders(wid, store)
    assert result["sync_status"] == "ok"
    assert captured.get("created_after")
    assert captured.get("update_after") is None
    assert "created_at" in str(captured.get("sort_by"))
