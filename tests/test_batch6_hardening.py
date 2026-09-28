"""Batch 6 — Gross Sales semantics, prod guards, probe tokens, redaction."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from src.finance_sync import classify_transaction, finance_summary
from src.log_redact import redact_mapping, redact_string
from src.production_guards import validate_production_environment


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
    from src.db import reset_repo_for_tests

    yield reset_repo_for_tests()


def test_gross_sales_not_double_counted_with_finance_ledger(tenancy_env):
    repo = tenancy_env
    wid = repo.create_workspace_with_owner("u-gs", "GS")["workspace"]["id"]
    store = repo.upsert_store(
        wid,
        {
            "store_id": "gs-store",
            "account": "gs@x.com",
            "seller_id": "seller-gs",
            "display_name": "GS",
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 86400,
        },
    )
    repo.upsert_daraz_order(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_order_id": "o-gs",
            "order_number": "o-gs",
            "status_group": "pending",
            "price": 200.0,
            "created_at_daraz": "2026-03-01T00:00:00",
        }
    )
    # Ledger amount deliberately different — must not add to Gross Sales
    repo.upsert_finance_transaction(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "store_slug": "gs-store",
            "source_transaction_id": "TX-GS",
            "transaction_type": "Orders-Settlements",
            "fee_type": "Commission",
            "amount": 9999.0,
            "fee_amount": 10.0,
            "currency": "PKR",
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    summary = finance_summary(wid)
    assert summary["gross_sales"] == 200.0
    assert summary["known_fees"] == 10.0
    assert summary["completeness"]["gross_sales_source"] == "orders"
    assert summary["completeness"]["finance_ledger_excluded_from_gross_sales"] is True


def test_unknown_transaction_excluded_from_fee_aggregate():
    unknown = classify_transaction(
        {
            "transaction_type": "MysteryLedger",
            "amount": 50,
            "fee_amount": None,
        }
    )
    assert unknown["include_in_gross_sales"] is False
    assert unknown["include_in_known_fees"] is False
    assert unknown["classification"] == "unknown"

    fee = classify_transaction(
        {
            "transaction_type": "Orders-Settlements",
            "fee_type": "Commission",
            "amount": 100,
            "fee_amount": 12,
        }
    )
    assert fee["include_in_known_fees"] is True
    assert fee["include_in_gross_sales"] is False


def test_production_env_rejects_test_mode(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_TEST_MODE", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("DARAZ_TOKEN_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    with pytest.raises(RuntimeError, match="AUTH_TEST_MODE"):
        validate_production_environment()


def test_production_env_rejects_memory_repo(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AUTH_TEST_MODE", raising=False)
    monkeypatch.setenv("TENANCY_REPO", "memory")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("DARAZ_TOKEN_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    with pytest.raises(RuntimeError, match="TENANCY_REPO"):
        validate_production_environment()


def test_production_env_ok(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AUTH_TEST_MODE", raising=False)
    monkeypatch.delenv("TENANCY_REPO", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("DARAZ_TOKEN_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    out = validate_production_environment()
    assert out["ok"] is True
    assert out["production"] is True


def test_create_probe_uses_workspace_client(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "true")
    repo = tenancy_env
    wid = repo.create_workspace_with_owner("u-probe", "P")["workspace"]["id"]
    src = repo.upsert_store(
        wid,
        {
            "store_id": "probe-a",
            "account": "a@x.com",
            "seller_id": "sa",
            "display_name": "A",
            "access_token": "tok-a",
            "refresh_token": "ref-a",
            "expires_in": 86400,
        },
    )
    dest = repo.upsert_store(
        wid,
        {
            "store_id": "probe-b",
            "account": "b@x.com",
            "seller_id": "sb",
            "display_name": "B",
            "access_token": "tok-b",
            "refresh_token": "ref-b",
            "expires_in": 86400,
        },
    )
    product = repo.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": src["id"],
            "daraz_item_id": "9001",
            "title": "Probe",
            "title_en": "Probe",
            "primary_category_id": 10001,
            "status_raw": "active",
            "detail_complete": True,
        }
    )
    repo.upsert_daraz_product_variant(
        {
            "workspace_id": wid,
            "store_id": src["id"],
            "product_id": product["id"],
            "daraz_sku_id": "sku-9001",
            "seller_sku": "SELL-9001",
            "price": 10,
            "quantity": 1,
        }
    )

    seen: dict = {}

    class FakeClient:
        def get_category_attributes(self, *_a, **_k):
            return {"data": {"attributes": []}}

        def query_category_brands(self, **_k):
            return {"module": [{"name": "No Brand", "brand_id": 1}]}

    def _client_for_store(store):
        seen["store_id"] = store.get("store_id")
        seen["has_access"] = bool(store.get("access_token"))
        return FakeClient()

    monkeypatch.setattr("src.ops.client_for_store", _client_for_store)
    monkeypatch.setattr("src.token_refresh.refresh_store_tokens", lambda **kw: None)
    monkeypatch.setattr(
        "src.product_create.build_connected_clone_draft",
        lambda *a, **k: {
            "draft": {
                "product": {"primary_category_id": 10001, "attributes": {}},
                "brand_resolution": {},
                "media": {"resolved_images": []},
                "variants": [],
            },
            "warnings": [],
            "errors": [],
            "possible_duplicates": [],
        },
    )
    monkeypatch.setattr(
        "src.product_create.validate_draft_against_category",
        lambda *a, **k: {"ok": True, "errors": []},
    )
    monkeypatch.setattr(
        "src.product_create.resolve_required_category_attributes",
        lambda *a, **k: {"ok": True},
    )
    monkeypatch.setattr(
        "src.product_create.resolve_brand_for_category",
        lambda **k: {"status": "OK", "brand": "No Brand"},
    )
    monkeypatch.setattr(
        "src.product_create.build_create_product_payload_preview",
        lambda *a, **k: {"xml_bytes": 1},
    )

    from src.product_create import run_supervised_create

    # Confirm dry-run reaches workspace client (not legacy token vault)
    out = run_supervised_create(
        wid,
        source_product_id=product["id"],
        destination_store_id=str(dest["store_id"]),
        confirm=True,
        execute=False,
    )
    assert seen.get("store_id") == "probe-b"
    assert seen.get("has_access") is True
    assert "token store" not in str(out.get("reason") or "").lower()


def test_log_redaction_hides_tokens_and_signed_urls():
    assert "[REDACTED]" in redact_string("Authorization: Bearer abc.def.ghi")
    assert "[REDACTED]" in redact_string(
        "https://api.example/x?access_token=supersecret&x=1"
    )
    redacted = redact_mapping(
        {"access_token": "tok", "nested": {"refresh_token": "r"}, "ok": "safe"}
    )
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["nested"]["refresh_token"] == "[REDACTED]"
    assert redacted["ok"] == "safe"


def test_bulk_pages_helper_removed():
    import src.ops as ops

    assert not hasattr(ops, "_bulk_pages_match_targets")


def test_rls_artifact_covers_finance_tables():
    from pathlib import Path

    text = Path("src/db/rls.sql").read_text(encoding="utf-8")
    assert "finance_transactions" in text
    assert "finance_payouts" in text
    assert "product_create_attempts" in text
    assert "trusted_workspace_connections" in text
    assert "backend" in text.lower() or "authoritative" in text.lower()
