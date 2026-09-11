"""Phase 2.5B store performance foundation tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.auth import issue_test_token
from src.db import reset_repo_for_tests
from src.performance_sync import (
    GROSS_SALES_ENABLED,
    fetch_orders_count_total,
    leaderboard_safe_view,
    rank_performance_rows,
    sync_store_month,
    sync_workspace_month,
)
from src.performance_time import (
    growth_pct,
    month_window_iso,
    previous_month,
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


def _bootstrap(client: TestClient, user_id: str, email: str) -> tuple[str, dict[str, str]]:
    headers = _auth(user_id, email)
    res = client.post("/api/bootstrap", headers=headers)
    assert res.status_code == 200, res.text
    wid = res.json()["workspace"]["id"]
    headers["X-Workspace-Id"] = wid
    return wid, headers


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


def test_month_window_uses_marketplace_plus0800():
    start, end = month_window_iso(2026, 9)
    assert start.startswith("2026-09-01T00:00:00+08:00")
    assert end.startswith("2026-10-01T00:00:00+08:00")
    assert previous_month(2026, 1) == (2025, 12)


def test_growth_pct_null_when_previous_zero():
    assert growth_pct(10, 0) is None
    assert growth_pct(0, 0) is None
    assert growth_pct(110, 100) == 10.0
    assert growth_pct(None, 5) is None


def test_count_total_parsing(monkeypatch):
    client = MagicMock()
    client.get_orders.side_effect = [
        {"data": {"countTotal": 306, "count": 1, "orders": [{"order_id": "1"}]}},  # all
        {"data": {"countTotal": 107, "count": 1, "orders": []}},  # canceled
    ]
    assert fetch_orders_count_total(client, year=2026, month=9) == 199
    assert client.get_orders.call_args_list[0].kwargs["status"] == "all"
    assert client.get_orders.call_args_list[1].kwargs["status"] == "canceled"
    assert all(c.kwargs.get("limit") == 1 for c in client.get_orders.call_args_list)
    assert "created_after" in client.get_orders.call_args_list[0].kwargs
    assert "created_before" in client.get_orders.call_args_list[0].kwargs


def test_orders_exclude_canceled_breakdown():
    from src.performance_sync import fetch_orders_count_breakdown, is_canceled_order

    assert is_canceled_order({"statuses": ["canceled"]}) is True
    assert is_canceled_order({"statuses": ["delivered"]}) is False

    client = MagicMock()
    client.get_orders.side_effect = [
        {"data": {"countTotal": 306}},
        {"data": {"countTotal": 107}},
    ]
    breakdown = fetch_orders_count_breakdown(client, year=2026, month=9)
    assert breakdown == {
        "orders_all_count": 306,
        "orders_canceled_count": 107,
        "orders_count": 199,
    }


def test_unique_store_month_and_workspace_isolation(tenancy_env):
    repo = tenancy_env
    wid_a = repo.create_workspace_with_owner("user-a", "A")["workspace"]["id"]
    wid_b = repo.create_workspace_with_owner("user-b", "B")["workspace"]["id"]
    store_a = _add_store(repo, wid_a, "store_a", "a@x.com")
    store_b = _add_store(repo, wid_b, "store_b", "b@x.com")

    repo.upsert_store_performance(
        workspace_id=wid_a,
        store_uuid=store_a["id"],
        year=2026,
        month=9,
        orders_count=10,
        orders_synced=True,
    )
    repo.upsert_store_performance(
        workspace_id=wid_a,
        store_uuid=store_a["id"],
        year=2026,
        month=9,
        orders_count=22,
        orders_synced=True,
    )
    rows_a = repo.list_store_performance(wid_a, 2026, 9)
    assert len(rows_a) == 1
    assert rows_a[0]["orders_count"] == 22

    repo.upsert_store_performance(
        workspace_id=wid_b,
        store_uuid=store_b["id"],
        year=2026,
        month=9,
        orders_count=99,
        orders_synced=True,
    )
    assert len(repo.list_store_performance(wid_a, 2026, 9)) == 1
    assert repo.list_store_performance(wid_a, 2026, 9)[0]["orders_count"] == 22
    assert repo.list_store_performance(wid_b, 2026, 9)[0]["orders_count"] == 99


def test_ranking_orders_then_gross_then_uuid():
    rows = [
        {"store_id": "bbb", "orders_count": 10, "gross_sales": 500},
        {"store_id": "aaa", "orders_count": 10, "gross_sales": 900},
        {"store_id": "ccc", "orders_count": 20, "gross_sales": 100},
    ]
    ranked = rank_performance_rows(rows, metric="orders")
    assert [r["store_id"] for r in ranked] == ["ccc", "aaa", "bbb"]
    assert [r["rank"] for r in ranked] == [1, 2, 3]

    ranked_g = rank_performance_rows(rows, metric="gross_sales")
    assert [r["store_id"] for r in ranked_g] == ["aaa", "bbb", "ccc"]


def test_safe_serializer_excludes_sensitive_fields():
    store = {
        "store_id": "acct_slug",
        "display_name": "Shop A",
        "account": "secret@email.com",
        "access_token": "tok",
        "seller_id": "SELLER",
    }
    row = {
        "store_id": "uuid-1",
        "workspace_id": "ws-1",
        "orders_count": 5,
        "gross_sales": 100.5,
        "currency": "PKR",
        "rank": 1,
        "year": 2026,
        "month": 9,
        "sync_status": "ok",
    }
    view = leaderboard_safe_view(row, store)
    assert view["store_id"] == "uuid-1"
    assert view["display_name"] == "Shop A"
    assert "workspace_id" not in view
    assert "account" not in view
    assert "access_token" not in view
    assert "seller_id" not in view
    assert "store_slug" not in view


def test_foreign_workspace_performance_forbidden(client: TestClient, tenancy_env):
    wid_a, headers_a = _bootstrap(client, "user-a", "a@example.com")
    wid_b, _headers_b = _bootstrap(client, "user-b", "b@example.com")
    store_b = _add_store(tenancy_env, wid_b, "store_b", "b@x.com")
    tenancy_env.upsert_store_performance(
        workspace_id=wid_b,
        store_uuid=store_b["id"],
        year=2026,
        month=9,
        orders_count=50,
        orders_synced=True,
    )

    # User A pointing at B's workspace id
    headers_a["X-Workspace-Id"] = wid_b
    res = client.get("/api/store-performance?year=2026&month=9", headers=headers_a)
    assert res.status_code == 403

    # User A with own workspace cannot see B's rows
    headers_a["X-Workspace-Id"] = wid_a
    res = client.get("/api/store-performance?year=2026&month=9", headers=headers_a)
    assert res.status_code == 200
    assert res.json()["leaderboard"] == []


def test_unauthorized_refresh(client: TestClient):
    assert client.post("/api/store-performance/sync?year=2026&month=9").status_code == 401
    assert client.get("/api/store-performance").status_code == 401


def test_partial_store_sync_failures(tenancy_env, monkeypatch):
    repo = tenancy_env
    wid = repo.create_workspace_with_owner("user-a", "A")["workspace"]["id"]
    ok_store = _add_store(repo, wid, "ok_store", "ok@x.com")
    bad_store = _add_store(repo, wid, "bad_store", "bad@x.com")

    from src.daraz_api import DarazApiError  # noqa: F401 — documents expected error path

    def fake_sync(**kwargs):
        store = kwargs["store"]
        if store["store_id"] == "bad_store":
            return {
                "store_id": store["id"],
                "sync_status": "error",
                "sync_error": "daraz:AuthError",
                "orders_count": 0,
            }
        return repo.upsert_store_performance(
            workspace_id=wid,
            store_uuid=store["id"],
            year=2026,
            month=9,
            orders_count=12,
            orders_synced=True,
            sync_status="ok",
        )

    monkeypatch.setattr("src.performance_sync.sync_store_month", fake_sync)
    summary = sync_workspace_month(wid, year=2026, month=9, include_gross_sales=False)
    assert summary["stores_total"] == 2
    assert summary["stores_ok"] == 1
    assert summary["stores_error"] == 1
    rows = repo.list_store_performance(wid, 2026, 9)
    assert len(rows) == 1
    assert rows[0]["store_id"] == ok_store["id"]
    assert rows[0]["orders_count"] == 12
    assert bad_store["id"]  # present in workspace; no zero row persisted


def test_sync_uses_count_total_not_body_scan(tenancy_env, monkeypatch):
    repo = tenancy_env
    wid = repo.create_workspace_with_owner("user-a", "A")["workspace"]["id"]
    store = _add_store(repo, wid, "s1", "s1@x.com")

    mock_client = MagicMock()
    mock_client.timeout = 30
    mock_client.get_orders.side_effect = [
        {"data": {"countTotal": 12, "orders": []}},  # current all
        {"data": {"countTotal": 5, "orders": []}},  # current canceled → net 7
        {"data": {"countTotal": 8, "orders": []}},  # previous all
        {"data": {"countTotal": 3, "orders": []}},  # previous canceled → net 5
    ]

    monkeypatch.setattr("src.performance_sync.client_for_store", lambda s: mock_client)
    monkeypatch.setattr(
        "src.performance_sync.access_token_expires_soon", lambda *a, **k: False
    )
    monkeypatch.setattr("src.performance_sync.GROSS_SALES_ENABLED", False)

    row = sync_store_month(
        workspace_id=wid,
        store=store,
        year=2026,
        month=9,
        include_gross_sales=False,
    )
    assert row["orders_count"] == 7
    assert row["previous_orders_count"] == 5
    assert row["orders_growth_pct"] == growth_pct(7, 5)
    # Count probes only (limit=1): 2 for current + 2 for previous when GS disabled
    assert all(c.kwargs.get("limit") == 1 for c in mock_client.get_orders.call_args_list)
    assert len(mock_client.get_orders.call_args_list) == 4


def test_gross_sales_flag_documented():
    # Phase 2.5B decision: ENABLED after offset 0/100/200 validation (no overlap).
    assert GROSS_SALES_ENABLED is True


def test_api_leaderboard_shape(client: TestClient, tenancy_env):
    wid, headers = _bootstrap(client, "user-a", "a@example.com")
    store = _add_store(tenancy_env, wid, "shop_one", "one@x.com")
    tenancy_env.upsert_store_performance(
        workspace_id=wid,
        store_uuid=store["id"],
        year=2026,
        month=9,
        orders_count=40,
        gross_sales=1200.0,
        currency="PKR",
        orders_growth_pct=25.0,
        orders_synced=True,
        gross_synced=True,
        sync_status="ok",
    )
    res = client.get("/api/store-performance?year=2026&month=9&metric=orders", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["gross_sales_enabled"] is True
    assert len(body["leaderboard"]) == 1
    item = body["leaderboard"][0]
    assert item["rank"] == 1
    assert item["orders_count"] == 40
    assert item["display_name"] == "shop_one"
    assert "workspace_id" not in item
    assert "access_token" not in item
