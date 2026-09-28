"""Batch 5 — Inventory + Analytics + Finance."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.analytics_service import analytics_dashboard
from src.auth import issue_test_token
from src.authorization import has_capability
from src.db import reset_repo_for_tests
from src.finance_sync import finance_summary, map_transaction_row, sync_finance
from src.inventory import inventory_summary, list_inventory, stock_badge


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
    yield reset_repo_for_tests()


def _client(user_id: str = "u-b5"):
    from src.app import app

    return TestClient(app), {"Authorization": f"Bearer {issue_test_token(user_id, email=f'{user_id}@x.com')}"}


def _bootstrap(repo, user_id: str = "u-b5"):
    client, headers = _client(user_id)
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
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _seed_product(repo, workspace_id: str, store: dict, *, item_id: str, qty, status="active"):
    product = repo.upsert_daraz_product(
        {
            "workspace_id": workspace_id,
            "store_id": store["id"],
            "daraz_item_id": item_id,
            "title": f"Product {item_id}",
            "title_en": f"Product {item_id}",
            "status_raw": status,
            "detail_complete": True,
            "synced_at": "2026-01-10T00:00:00+00:00",
        }
    )
    repo.upsert_daraz_product_variant(
        {
            "workspace_id": workspace_id,
            "store_id": store["id"],
            "product_id": product["id"],
            "daraz_sku_id": f"sku-{item_id}",
            "seller_sku": f"SELL-{item_id}",
            "price": 100,
            "quantity": qty,
            "sale_props_json": {"Color": "Red"},
            "synced_at": "2026-01-10T00:00:00+00:00",
        }
    )
    return product


def _seed_order(repo, workspace_id: str, store: dict, *, order_id: str, created: str, price: float, status="pending"):
    return repo.upsert_daraz_order(
        {
            "workspace_id": workspace_id,
            "store_id": store["id"],
            "daraz_order_id": order_id,
            "order_number": order_id,
            "status_group": status,
            "price": price,
            "created_at_daraz": created,
        }
    )


# ---- Inventory -------------------------------------------------------------


def test_stock_badge_unknown_vs_zero():
    assert stock_badge(None) == "Unknown"
    assert stock_badge(0) == "Out of Stock"
    assert stock_badge(3, low_stock_threshold=5) == "Low Stock"
    assert stock_badge(10) == "In Stock"


def test_inventory_workspace_isolation_and_unknown_qty(tenancy_env):
    repo = tenancy_env
    wid_a, client, headers = _bootstrap(repo, "u-inv-a")
    wid_b, client_b, headers_b = _bootstrap(repo, "u-inv-b")
    store_a = _add_store(repo, wid_a, "store-a")
    store_b = _add_store(repo, wid_b, "store-b")
    _seed_product(repo, wid_a, store_a, item_id="1", qty=None)
    _seed_product(repo, wid_a, store_a, item_id="2", qty=0)
    _seed_product(repo, wid_b, store_b, item_id="9", qty=50)

    page = list_inventory(wid_a)
    assert page["total"] == 2
    qtys = {i["daraz_item_id"]: i["quantity"] for i in page["items"]}
    assert qtys["1"] is None
    assert qtys["2"] == 0
    badges = {i["daraz_item_id"]: i["stock_badge"] for i in page["items"]}
    assert badges["1"] == "Unknown"
    assert badges["2"] == "Out of Stock"

    other = client_b.get("/api/inventory", headers=headers_b)
    assert other.status_code == 200
    assert other.json()["total"] == 1
    assert all(i["daraz_item_id"] != "1" for i in other.json()["items"])


def test_inventory_pagination_filters_low_stock(tenancy_env):
    repo = tenancy_env
    wid, client, headers = _bootstrap(repo)
    store = _add_store(repo, wid, "inv-store")
    for i, qty in enumerate([1, 2, 3, 20, None], start=1):
        _seed_product(repo, wid, store, item_id=str(i), qty=qty)

    page1 = client.get("/api/inventory?page=1&page_size=2&sort=quantity", headers=headers)
    assert page1.status_code == 200
    body = page1.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    low = client.get(
        "/api/inventory?low_stock=true&low_stock_threshold=5", headers=headers
    )
    assert low.status_code == 200
    # known low stock only (1,2,3) — excludes None and 20
    assert low.json()["total"] == 3

    summary = inventory_summary(wid, low_stock_threshold=5)
    assert summary["total_skus"] == 5
    assert summary["known_low_stock_skus"] == 3
    assert summary["out_of_stock_skus"] == 0
    assert summary["unknown_quantity_skus"] == 1

    search = client.get("/api/inventory?search=SELL-2", headers=headers)
    assert search.json()["total"] == 1


# ---- Analytics -------------------------------------------------------------


def test_analytics_month_comparison_and_isolation(tenancy_env):
    repo = tenancy_env
    wid, client, headers = _bootstrap(repo, "u-an")
    store = _add_store(repo, wid, "an-store")
    _seed_order(repo, wid, store, order_id="o1", created="2026-03-05T10:00:00", price=100, status="pending")
    _seed_order(repo, wid, store, order_id="o2", created="2026-03-12T10:00:00", price=200, status="shipped")
    _seed_order(repo, wid, store, order_id="o3", created="2026-02-10T10:00:00", price=50, status="pending")

    dash = analytics_dashboard(wid, year=2026, month=3)
    assert dash["summary"]["orders"] == 2
    assert dash["summary"]["gross_sales"] == 300.0
    assert dash["summary"]["average_order_value"] == 150.0
    assert dash["summary"]["previous_orders"] == 1
    assert dash["summary"]["mom_orders_pct"] == 100.0
    assert len(dash["orders_over_time"]) == 31
    assert dash["status_distribution"]
    assert dash["store_comparison"][0]["orders"] == 2
    assert "profit" not in (dash.get("label") or "").lower()

    # No previous data → N/A (null)
    empty_prev = analytics_dashboard(wid, year=2026, month=2)
    # February has data; January has none
    jan = analytics_dashboard(wid, year=2026, month=1)
    assert jan["summary"]["orders"] == 0
    assert jan["summary"]["mom_orders_pct"] is None
    assert jan["summary"]["mom_gross_sales_pct"] is None

    # Zero previous with current activity → still N/A (not infinite)
    assert empty_prev["summary"]["previous_orders"] == 0
    assert empty_prev["summary"]["mom_orders_pct"] is None

    api = client.get("/api/analytics/summary?year=2026&month=3", headers=headers)
    assert api.status_code == 200
    assert api.json()["summary"]["orders"] == 2

    # Isolation
    wid2, client2, headers2 = _bootstrap(repo, "u-an2")
    other = client2.get("/api/analytics/summary?year=2026&month=3", headers=headers2)
    assert other.json()["summary"]["orders"] == 0


# ---- Finance ---------------------------------------------------------------


def test_finance_capabilities(tenancy_env):
    assert has_capability("owner", "finance.sync")
    assert has_capability("manager", "finance.read")
    assert not has_capability("manager", "finance.sync")
    assert not has_capability("viewer", "finance.read")


def test_finance_sync_idempotent_partial_and_apis(tenancy_env):
    repo = tenancy_env
    wid, client, headers = _bootstrap(repo, "u-fin")
    store = _add_store(repo, wid, "fin-store")
    store2 = _add_store(repo, wid, "fin-store-2")

    class FakeClient:
        def get_finance_transaction_details(self, **kwargs):
            return {
                "data": {
                    "transactions": [
                        {
                            "transaction_number": "TX-1",
                            "order_no": "100",
                            "transaction_type": "Orders-Settlements",
                            "fee_type": "Commission",
                            "amount": "500",
                            "fee_amount": "25",
                            "currency": "PKR",
                            "transaction_date": "2026-03-01",
                        }
                    ]
                }
            }

        def get_payout_status(self, **kwargs):
            return {
                "data": {
                    "payouts": [
                        {
                            "statement_number": "ST-1",
                            "payout": "400",
                            "paid": True,
                            "created_at": "2026-03-02",
                        }
                    ]
                }
            }

    with patch("src.finance_sync.refresh_store_tokens", return_value=None), patch(
        "src.finance_sync.client_for_store", side_effect=[FakeClient(), Exception("boom")]
    ):
        result = sync_finance(
            wid,
            store_ids=["fin-store", "fin-store-2"],
            actor_user_id="u-fin",
        )
    assert result["status"] == "partial"
    assert result["transactions_synced"] == 1
    assert result["payouts_synced"] == 1
    assert result["partial"] is True

    # Idempotent upsert
    mapped = map_transaction_row(
        workspace_id=wid,
        store_uuid=str(store["id"]),
        store_slug="fin-store",
        raw={
            "transaction_number": "TX-1",
            "amount": "500",
            "fee_amount": "25",
            "currency": "PKR",
        },
    )
    repo.upsert_finance_transaction(mapped)
    repo.upsert_finance_transaction(mapped)
    listed = repo.list_finance_transactions(wid, page=1, page_size=50)
    assert listed["total"] == 1
    assert listed["items"][0]["amount"] == 500.0

    # Unknown remains unknown in summary (no fabricated zero fees from empty)
    empty_ws, client_e, headers_e = _bootstrap(repo, "u-fin-empty")
    empty_sum = finance_summary(empty_ws)
    assert empty_sum["gross_sales"] is None
    assert empty_sum["known_fees"] is None
    assert empty_sum["known_payouts"] is None

    sum_api = client.get("/api/finance/summary", headers=headers)
    assert sum_api.status_code == 200
    body = sum_api.json()
    assert body["gross_sales"] == 500.0
    assert body["known_fees"] == 25.0
    assert body["known_payouts"] == 400.0
    assert "profit" not in body

    tx_api = client.get("/api/finance/transactions?page=1&page_size=10", headers=headers)
    assert tx_api.status_code == 200
    assert tx_api.json()["total"] == 1

    po_api = client.get("/api/finance/payouts", headers=headers)
    assert po_api.status_code == 200
    assert po_api.json()["total"] == 1

    # Empty selection rejected
    bad = client.post("/api/finance/sync", headers=headers, json={"store_ids": []})
    assert bad.status_code == 400

    # Workspace isolation
    wid2, client2, headers2 = _bootstrap(repo, "u-fin2")
    iso = client2.get("/api/finance/summary", headers=headers2)
    assert iso.status_code == 200
    assert iso.json()["transaction_count"] == 0


def test_finance_capability_enforcement_http(tenancy_env):
    repo = tenancy_env
    wid, client, headers = _bootstrap(repo, "u-owner-fin")
    # Add member without finance caps
    repo.add_member(wid, "u-viewer-fin", "viewer")
    viewer_headers = {
        "Authorization": f"Bearer {issue_test_token('u-viewer-fin', email='v@x.com')}",
        "X-Workspace-Id": wid,
    }
    denied = client.get("/api/finance/summary", headers=viewer_headers)
    assert denied.status_code == 403

    sync_denied = client.post(
        "/api/finance/sync",
        headers=viewer_headers,
        json={"store_ids": ["x"]},
    )
    assert sync_denied.status_code == 403


def test_finance_token_refresh_path_called(tenancy_env):
    repo = tenancy_env
    wid, _, _ = _bootstrap(repo, "u-ref")
    _add_store(repo, wid, "ref-store")
    called = {}

    class EmptyClient:
        def get_finance_transaction_details(self, **kwargs):
            return {"data": {"transactions": []}}

        def get_payout_status(self, **kwargs):
            return {"data": {"payouts": []}}

    def _refresh(**kwargs):
        called["ok"] = True
        called["stores"] = kwargs.get("store_ids")

    with patch("src.finance_sync.refresh_store_tokens", side_effect=_refresh), patch(
        "src.finance_sync.client_for_store", return_value=EmptyClient()
    ):
        sync_finance(wid, store_ids=["ref-store"], actor_user_id="u-ref")
    assert called.get("ok") is True
    assert called.get("stores") == ["ref-store"]
