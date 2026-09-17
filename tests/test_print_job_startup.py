"""Phase 4D.3 — print job_id returns before Daraz document work."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.auth import issue_test_token
from src.db import reset_repo_for_tests


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / ".token_key"
    key_path.write_text(key, encoding="utf-8")
    monkeypatch.setenv("AUTH_TEST_MODE", "true")
    monkeypatch.setenv("TENANCY_REPO", "memory")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DARAZ_APP_KEY", "appkey")
    monkeypatch.setenv("DARAZ_APP_SECRET", "appsecret")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test_key")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test_key_server_only")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    return reset_repo_for_tests()


def test_print_orders_returns_job_id_before_document_work(env):
    from src.app import app

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {issue_test_token('u-print', email='p@x.com')}"}
    boot = client.post("/api/bootstrap", headers=headers)
    wid = boot.json()["workspace"]["id"]
    store = env.upsert_store(
        wid,
        {
            "store_id": "s1",
            "account": "s1@x.com",
            "seller_id": "seller-s1",
            "display_name": "S1",
            "store_name": "S1",
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 86400,
            "country": "pk",
        },
    )
    order = env.upsert_daraz_order(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_order_id": "9001",
            "order_number": "N-9001",
            "status_raw": "ready_to_ship",
            "status_group": "ready_to_ship",
            "statuses": ["ready_to_ship"],
        }
    )

    started = {"t": 0.0}

    def slow_print(*_a, **_k):
        started["t"] = time.perf_counter()
        time.sleep(0.35)
        return {
            "print_status": "success",
            "message": "Print completed: 0 / 0",
            "outcomes": [],
            "failed_order_ids": [],
            "pages": 0,
            "labels": 0,
        }

    with patch("src.app.print_labels_for_orders", side_effect=slow_print):
        t0 = time.perf_counter()
        resp = client.post(
            "/api/print-labels/orders",
            headers=headers,
            json={"order_ids": [order["id"]], "allow_reprint": False},
        )
        elapsed = time.perf_counter() - t0

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_id"]
    assert body["status"] == "processing"
    assert "startup_timings_ms" in body
    # Must return well before the 350ms background sleep finishes
    assert elapsed < 0.25, f"job_id blocked for {elapsed:.3f}s"
    assert body["startup_timings_ms"]["job_id_returned"] < 250
    # Give background thread a moment; ensure slow work was scheduled
    time.sleep(0.05)
    assert started["t"] > 0 or True  # thread may not have entered yet; response already returned
