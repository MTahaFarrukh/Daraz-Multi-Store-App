"""Store health: token expiry vs verified read-only /seller/get."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.store_api_health import probe_store_api_health
from src.token_store import connection_health, sanitize_store_view


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


def _store(repo, wid: str, store_id: str = "s1", *, days_left: int = 30):
    now = datetime.now(UTC)
    return repo.upsert_store(
        wid,
        {
            "store_id": store_id,
            "account": f"{store_id}@x.com",
            "seller_id": "seller-1",
            "display_name": store_id,
            "store_name": store_id,
            "access_token": "access-token-value",
            "refresh_token": "refresh-token-value",
            "expires_in": days_left * 86400,
            "refresh_expires_in": 60 * 86400,
            "access_token_expires_at": (now + timedelta(days=days_left)).isoformat(),
            "refresh_token_expires_at": (now + timedelta(days=60)).isoformat(),
            "country": "pk",
        },
    )


def test_sanitize_list_view_marks_api_unverified(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    row = _store(tenancy_env, wid, days_left=40)
    view = sanitize_store_view(row)
    assert view["api_health"] == "unverified"
    assert view["token_status"] == "ok"
    assert "access_token" not in view
    assert "refresh_token" not in view


def test_token_ok_is_not_api_verified(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    row = _store(tenancy_env, wid, days_left=40)
    health = connection_health(row)
    assert health["token_status"] == "ok"
    assert health["needs_attention"] is False
    # Explicit: expiry alone never implies API connectivity
    assert "api_health" not in health or health.get("api_health") != "ok"


def test_probe_seller_get_success(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _store(tenancy_env, wid, days_left=40)

    client = MagicMock()
    client.get_seller.return_value = {
        "code": "0",
        "data": {"name": "Demo Shop", "seller_id": "99"},
    }
    monkeypatch.setattr("src.ops.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **_k: []
    )

    out = probe_store_api_health(wid, "s1")
    assert out["ok"] is True
    assert out["api_health"] == "ok"
    assert out["token_status"] == "ok"
    assert out["probe"]["api"] == "/seller/get"
    assert out["seller"]["name"] == "Demo Shop"
    store_view = out["store"] or {}
    assert "access_token" not in store_view
    assert "refresh_token" not in store_view
    client.get_seller.assert_called_once_with()


def test_probe_seller_get_error(tenancy_env, monkeypatch):
    from src.daraz_api import DarazApiError

    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _store(tenancy_env, wid, days_left=40)

    client = MagicMock()
    client.get_seller.side_effect = DarazApiError("nope", code="IllegalAccessToken")
    monkeypatch.setattr("src.ops.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **_k: []
    )

    out = probe_store_api_health(wid, "s1")
    assert out["ok"] is False
    assert out["api_health"] == "error"
    assert out["token_status"] == "ok"  # token still looks fine; API failed
    assert "IllegalAccessToken" in str(out.get("reason") or "")


def test_probe_skips_when_refresh_expired(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    now = datetime.now(UTC)
    tenancy_env.upsert_store(
        wid,
        {
            "store_id": "dead",
            "account": "dead@x.com",
            "seller_id": "x",
            "display_name": "dead",
            "store_name": "dead",
            "access_token": "a",
            "refresh_token": "r",
            "access_token_expires_at": (now - timedelta(days=1)).isoformat(),
            "refresh_token_expires_at": (now - timedelta(days=1)).isoformat(),
            "country": "pk",
        },
    )
    called = []

    def _no_client(*_a, **_k):
        called.append(1)
        raise AssertionError("must not call Daraz when refresh is dead")

    monkeypatch.setattr("src.ops.client_for_store", _no_client)
    out = probe_store_api_health(wid, "dead")
    assert out["api_health"] == "skipped"
    assert out["ok"] is False
    assert called == []
