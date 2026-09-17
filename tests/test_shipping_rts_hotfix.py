"""Phase 3 hotfix — live Shipping RTS tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.auth import issue_test_token
from src.db import reset_repo_for_tests
from src.shipping_rts import fetch_store_live_rts, load_shipping_rts


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
    return reset_repo_for_tests()


@pytest.fixture()
def client(tenancy_env):
    from src.app import app

    return TestClient(app)


def _auth(user_id: str = "u1", email: str = "a@example.com") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_test_token(user_id, email=email)}"}


def _bootstrap(client: TestClient) -> str:
    res = client.post("/api/bootstrap", headers=_auth())
    assert res.status_code == 200
    return res.json()["workspace"]["id"]


def _add_store(repo, wid: str, store_id: str) -> dict[str, Any]:
    return repo.upsert_store(
        wid,
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


def _daraz_order(oid: str, created: str = "2026-09-16 10:00:00 +0800") -> dict:
    return {
        "order_id": oid,
        "order_number": f"ON-{oid}",
        "statuses": ["ready_to_ship"],
        "created_at": created,
        "updated_at": created,
        "price": "100",
        "currency": "PKR",
        "items_count": 1,
    }


def test_load_rts_does_not_require_local_sync(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "mtf")

    mock_client = MagicMock()
    mock_client.timeout = 45.0
    mock_client.get_orders.return_value = {
        "code": "0",
        "data": {"countTotal": 1, "orders": [_daraz_order("111")]},
    }
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )

    # Local warehouse empty / stale order that is NOT live RTS
    tenancy_env.upsert_daraz_order(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_order_id": "STALE-YDAY",
            "status_group": "ready_to_ship",
            "status_raw": "ready_to_ship",
            "created_at_daraz": "2026-09-15T10:00:00+08:00",
        }
    )

    result = load_shipping_rts(wid, store_ids=["mtf"])
    ids = {o["daraz_order_id"] for o in result["orders"]}
    assert "111" in ids
    assert "STALE-YDAY" not in ids
    assert result["source"] == "live_daraz_rts"
    assert result["stores_ok"] == 1


def test_live_rts_missing_locally_still_appears(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "mtf")
    mock_client = MagicMock()
    mock_client.timeout = 45.0
    mock_client.get_orders.return_value = {
        "code": "0",
        "data": {"countTotal": 1, "orders": [_daraz_order("TODAY-1")]},
    }
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    result = load_shipping_rts(wid, store_ids=["mtf"])
    assert result["orders"][0]["daraz_order_id"] == "TODAY-1"
    assert result["orders"][0]["id"]  # upserted for print path


def test_print_state_reconcile_unprinted_and_printed(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "mtf")
    # Seed prior print for one order
    existing = tenancy_env.upsert_daraz_order(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_order_id": "PRINTED-1",
            "status_group": "ready_to_ship",
        }
    )
    tenancy_env.insert_label_print(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "order_id": existing["id"],
            "daraz_order_id": "PRINTED-1",
            "is_reprint": False,
        }
    )

    mock_client = MagicMock()
    mock_client.timeout = 45.0
    mock_client.get_orders.return_value = {
        "code": "0",
        "data": {
            "countTotal": 2,
            "orders": [_daraz_order("PRINTED-1"), _daraz_order("NEW-2")],
        },
    }
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    result = load_shipping_rts(wid, store_ids=["mtf"])
    by_id = {o["daraz_order_id"]: o for o in result["orders"]}
    assert by_id["PRINTED-1"]["has_print"] is True
    assert by_id["PRINTED-1"]["print_count"] >= 1
    assert by_id["NEW-2"]["has_print"] is False
    assert result["unprinted_count"] == 1


def test_one_store_failure_keeps_successful(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "ok_store")
    _add_store(tenancy_env, wid, "bad_store")

    def _client_for(store):
        client = MagicMock()
        client.timeout = 45.0
        if store.get("store_id") == "bad_store":
            from src.daraz_api import DarazApiError

            client.get_orders.side_effect = DarazApiError("fail", code="500")
        else:
            client.get_orders.return_value = {
                "code": "0",
                "data": {"countTotal": 1, "orders": [_daraz_order("OK-1")]},
            }
        return client

    monkeypatch.setattr("src.shipping_rts.client_for_store", _client_for)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    result = load_shipping_rts(wid, store_ids=["ok_store", "bad_store"])
    assert result["partial"] is True
    assert result["stores_ok"] == 1
    assert result["stores_failed"] == 1
    assert len(result["orders"]) == 1


def test_empty_selection_rejected(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    with pytest.raises(ValueError, match="No stores"):
        load_shipping_rts(wid, store_ids=[])


def test_count_total_mismatch_marks_incomplete(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "mtf")
    mock_client = MagicMock()
    mock_client.timeout = 45.0
    mock_client.get_orders.return_value = {
        "code": "0",
        "data": {"countTotal": 57, "orders": [_daraz_order("ONLY-20")]},
    }
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    # Single page returns 1 < PAGE_SIZE so loop stops, but countTotal=57 → incomplete
    result = fetch_store_live_rts(wid, store)
    assert result["incomplete"] is True
    assert result["ok"] is False


def test_older_created_still_rts_included(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "mtf")
    mock_client = MagicMock()
    mock_client.timeout = 45.0
    mock_client.get_orders.return_value = {
        "code": "0",
        "data": {
            "countTotal": 1,
            "orders": [_daraz_order("OLD", created="2026-08-01 10:00:00 +0800")],
        },
    }
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    result = load_shipping_rts(wid, store_ids=["mtf"])
    assert result["orders"][0]["daraz_order_id"] == "OLD"
    # Primary uses update_after (not "today only"); older-created still returned by API
    kwargs = mock_client.get_orders.call_args.kwargs
    assert kwargs.get("status") == "ready_to_ship"
    assert kwargs.get("update_after")
    assert kwargs.get("created_after") is None


def test_api_shipping_rts_endpoint(client, tenancy_env, monkeypatch):
    wid = _bootstrap(client)
    _add_store(tenancy_env, wid, "mtf")
    headers = _auth()
    headers["X-Workspace-Id"] = wid

    mock_client = MagicMock()
    mock_client.timeout = 45.0
    mock_client.get_orders.return_value = {
        "code": "0",
        "data": {"countTotal": 1, "orders": [_daraz_order("API-1")]},
    }
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )

    res = client.post(
        "/api/shipping/rts",
        headers=headers,
        json={"store_ids": ["mtf"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 1
    assert body["orders"][0]["daraz_order_id"] == "API-1"

    bad = client.post("/api/shipping/rts", headers=headers, json={"store_ids": []})
    assert bad.status_code == 422


def test_credential_isolation_fresh_client_per_store(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "a")
    _add_store(tenancy_env, wid, "b")
    seen: list[str] = []

    def _client_for(store):
        seen.append(store.get("store_id"))
        client = MagicMock()
        client.timeout = 45.0
        client.get_orders.return_value = {
            "code": "0",
            "data": {
                "countTotal": 1,
                "orders": [_daraz_order(f"O-{store.get('store_id')}")],
            },
        }
        return client

    monkeypatch.setattr("src.shipping_rts.client_for_store", _client_for)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    load_shipping_rts(wid, store_ids=["a", "b"])
    assert set(seen) == {"a", "b"}


def test_orchestration_wall_is_not_sum_of_stores(tenancy_env, monkeypatch):
    """Concurrent stores: wall_total ≈ slowest store, not sum of durations."""
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "fast")
    _add_store(tenancy_env, wid, "slow")

    def _fake_fetch(workspace_id, store, **_k):
        slug = str(store.get("store_id") or "")
        elapsed = 50 if slug == "fast" else 200
        return {
            "store_uuid": store.get("id"),
            "store_id": slug,
            "display_name": slug,
            "ok": True,
            "incomplete": False,
            "error": None,
            "countTotal": 0,
            "unique": 0,
            "returned": 0,
            "elapsed_ms": elapsed,
            "orders": [],
            "timings_ms": {"total": elapsed, "orders_get": elapsed},
            "request_timings": [],
            "pages": 0,
            "window_mode": "update_after",
        }

    monkeypatch.setattr("src.shipping_rts.fetch_store_live_rts", _fake_fetch)
    result = load_shipping_rts(wid, store_ids=["fast", "slow"])
    store_sum = sum(s["elapsed_ms"] for s in result["stores"])
    assert store_sum == 250
    assert result["timings_ms"]["slowest_store"] == 200
    # Wall must not equal the arithmetic sum of per-store durations.
    assert result["timings_ms"]["wall_total"] < store_sum
    assert result["elapsed_ms"] == result["timings_ms"]["wall_total"]


def test_pagination_stops_when_count_fits_one_page(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "mtf")
    orders = [_daraz_order(str(i)) for i in range(24)]
    mock_client = MagicMock()
    mock_client.timeout = 45.0
    mock_client.get_orders.return_value = {
        "code": "0",
        "data": {"countTotal": 24, "orders": orders},
    }
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    result = fetch_store_live_rts(wid, store)
    assert mock_client.get_orders.call_count == 1
    assert result["pages"] == 1
    assert result["returned"] == 24
    assert result["request_timings"][0]["returned_count"] == 24
    assert result["request_timings"][0]["countTotal"] == 24
    assert "api_ms" in result["request_timings"][0]


def test_pagination_stops_when_returned_lt_page_size(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "mtf")
    mock_client = MagicMock()
    mock_client.timeout = 45.0
    mock_client.get_orders.return_value = {
        "code": "0",
        "data": {
            "countTotal": 3,
            "orders": [_daraz_order("1"), _daraz_order("2"), _daraz_order("3")],
        },
    }
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    result = fetch_store_live_rts(wid, store)
    assert mock_client.get_orders.call_count == 1
    assert result["pages"] == 1


def test_update_after_primary_expand_when_empty(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "mtf")
    mock_client = MagicMock()
    mock_client.timeout = 45.0

    def _get_orders(**kwargs):
        if kwargs.get("update_after"):
            return {"code": "0", "data": {"countTotal": 0, "orders": []}}
        return {
            "code": "0",
            "data": {"countTotal": 1, "orders": [_daraz_order("EXPANDED")]},
        }

    mock_client.get_orders.side_effect = _get_orders
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    result = fetch_store_live_rts(wid, store)
    assert result["window_mode"] == "created_after_expand"
    assert result["orders"][0]["daraz_order_id"] == "EXPANDED"
    assert mock_client.get_orders.call_count == 2
    first = mock_client.get_orders.call_args_list[0].kwargs
    second = mock_client.get_orders.call_args_list[1].kwargs
    assert first.get("update_after")
    assert first.get("created_after") is None
    assert second.get("created_after")
    assert second.get("update_after") is None
    assert len(result["request_timings"]) == 2
    assert result["request_timings"][0]["mode"] == "update_after"
    assert result["request_timings"][1]["mode"] == "created_after"


def test_update_after_primary_used_when_nonempty(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "mtf")
    mock_client = MagicMock()
    mock_client.timeout = 45.0
    mock_client.get_orders.return_value = {
        "code": "0",
        "data": {"countTotal": 1, "orders": [_daraz_order("HIT")]},
    }
    monkeypatch.setattr("src.shipping_rts.client_for_store", lambda _s: mock_client)
    monkeypatch.setattr(
        "src.shipping_rts.access_token_expires_soon", lambda *_a, **_k: False
    )
    result = fetch_store_live_rts(wid, store)
    assert result["window_mode"] == "update_after"
    assert mock_client.get_orders.call_count == 1
    kwargs = mock_client.get_orders.call_args.kwargs
    assert kwargs.get("update_after")
    assert kwargs.get("created_after") is None
