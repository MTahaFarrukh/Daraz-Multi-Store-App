"""Correlation ID + safe diagnostics for Add Product (no live Daraz calls)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.db import reset_repo_for_tests
from src.product_add_trace import ProductAddTrace, safe_error_detail
from src.public_daraz import clear_public_cache_for_tests


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
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_public_cache_for_tests()
    return reset_repo_for_tests()


def test_safe_error_detail_never_embeds_stack():
    detail = safe_error_detail("boom", diagnostic_id="cid-1", status_code=400)
    assert detail["diagnostic_id"] == "cid-1"
    assert detail["message"] == "boom"
    assert "traceback" not in str(detail).lower()


def test_trace_redacts_credential_like_fields():
    trace = ProductAddTrace.create("workspace-abcdef")
    trace.stage("request_received", message="token=supersecret value")
    hist = trace.public_history()
    assert hist[0]["stage"] == "request_received"
    assert hist[0]["message"] == "[redacted]"


def test_add_from_url_validation_error_returns_diagnostic_id(tenancy_env):
    from src.app import app
    from src.auth import issue_test_token

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {issue_test_token('u-diag', email='d@x.com')}"}
    boot = client.post("/api/bootstrap", headers=headers)
    assert boot.status_code == 200

    with patch(
        "src.product_add.add_product_from_public_url",
        side_effect=ValueError("destination_store_ids is required"),
    ):
        res = client.post(
            "/api/products/add-from-url",
            headers=headers,
            json={
                "url": "https://www.daraz.pk/products/i123456.html",
                "destination_store_ids": ["store-a"],
                "execute": False,
            },
        )
    assert res.status_code == 400
    body = res.json()
    detail = body.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("diagnostic_id")
    assert "destination_store_ids" in str(detail.get("message") or "")
    assert "traceback" not in str(body).lower()


def test_add_from_url_success_includes_correlation_id(tenancy_env, monkeypatch):
    from src.app import app
    from src.auth import issue_test_token

    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")

    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {issue_test_token('u-diag2', email='d2@x.com')}"
    }
    boot = client.post("/api/bootstrap", headers=headers)
    assert boot.status_code == 200

    fake = {
        "status": "BLOCKED_CREATE",
        "destinations": [
            {
                "store": {"store_id": "s1", "display_name": "Store One"},
                "status": "NEEDS_ATTENTION",
                "reason": "missing_price",
            }
        ],
        "needs_attention_count": 1,
        "created_count": 0,
        "failed_count": 0,
        "product_create_enabled": False,
        "correlation_id": "fixed-cid",
        "stage_history": [{"stage": "gate_checked", "at_ms": 1}],
    }

    with patch("src.product_add.add_product_from_public_url", return_value=dict(fake)):
        with patch("src.app.audit_product_create_result"):
            res = client.post(
                "/api/products/add-from-url",
                headers=headers,
                json={
                    "url": "https://www.daraz.pk/products/i123456.html",
                    "destination_store_ids": ["s1"],
                    "execute": True,
                },
            )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("correlation_id")
    assert body.get("needs_attention_count") == 1
    assert body["destinations"][0]["reason"] == "missing_price"
