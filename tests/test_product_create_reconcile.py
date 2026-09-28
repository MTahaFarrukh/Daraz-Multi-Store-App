"""Batch 1 — product create reconciliation + safe retry."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.audit_log import audit_event, safe_metadata
from src.auth import issue_test_token
from src.daraz_api import DarazApiError
from src.db import reset_repo_for_tests
from src.product_create_reconcile import (
    apply_attempt_seller_skus,
    reconcile_product_create_attempt,
    retry_product_create_attempt,
)
from src.public_daraz import clear_public_cache_for_tests


@pytest.fixture()
def tenancy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / ".token_key"
    key_path.write_text(key, encoding="ascii")
    monkeypatch.setenv("AUTH_TEST_MODE", "true")
    monkeypatch.setenv("TENANCY_REPO", "memory")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test_key")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test_key_server_only")
    monkeypatch.setenv("DARAZ_APP_KEY", "appkey")
    monkeypatch.setenv("DARAZ_APP_SECRET", "appsecret")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "true")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_public_cache_for_tests()
    return reset_repo_for_tests()


def _boot(repo, user="u-b1") -> tuple[str, str, TestClient]:
    from src.app import app

    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {issue_test_token(user, email=f'{user}@x.com')}"
    }
    boot = client.post("/api/bootstrap", headers=headers)
    wid = boot.json()["workspace"]["id"]
    store = repo.upsert_store(
        wid,
        {
            "store_id": "store-a",
            "account": "a@x.com",
            "seller_id": "s-a",
            "display_name": "Store A",
            "access_token": "t",
            "refresh_token": "r",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )
    repo.upsert_product_defaults(
        wid,
        {
            "default_package_weight": 0.5,
            "default_package_length": 10,
            "default_package_width": 8,
            "default_package_height": 4,
            "default_initial_quantity": 1,
        },
    )
    return wid, str(store["id"]), client


def _attempt(repo, wid: str, dest_uuid: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "workspace_id": wid,
        "source_type": "public_daraz_url",
        "source_identity": "https://www.daraz.pk/products/x-i1.html",
        "destination_store_id": dest_uuid,
        "request_fingerprint": extra.pop("request_fingerprint", "fp-1"),
        "state": extra.pop("state", "NEEDS_RECONCILIATION"),
        "generated_seller_skus": extra.pop(
            "generated_seller_skus",
            [
                {"seller_sku": "MTF-101", "source_daraz_sku_id": "101"},
                {"seller_sku": "MTF-102", "source_daraz_sku_id": "102"},
            ],
        ),
        "verification_state": "UNVERIFIED",
    }
    payload.update(extra)
    return repo.create_product_attempt(payload)


def test_timeout_needs_reconciliation_blocks_second_create(tenancy_env):
    from src.product_add import add_product_from_public_url

    wid, dest_uuid, _ = _boot(tenancy_env)
    tenancy_env.upsert_store(
        wid,
        {
            "store_id": "store-a",
            "account": "a@x.com",
            "seller_id": "s-a",
            "display_name": "Store A",
            "access_token": "t",
            "refresh_token": "r",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )
    fake = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i99.html",
        "item_id": "99",
        "title": "Bag",
        "brand": "No Brand",
        "description_html": "<p>Bag</p>",
        "images": ["https://static-01.daraz.pk/p/a.jpg"],
        "category_id": "10001948",
        "category_resolution": {"confidence": "high", "source": "catalog"},
        "variants": [
            {
                "daraz_sku_id": "101",
                "price": 500,
                "sale_props": {"Color": "Black"},
                "dimensions": [
                    {
                        "source_property_name": "Color",
                        "source_value": "Black",
                        "semantic_type": "color",
                    }
                ],
                "price_source": "mtop.getDetailInfo",
            }
        ],
        "pricing_summary": {
            "regular_prices_complete": True,
            "missing_variant_prices": 0,
        },
        "timings_ms": {},
        "warnings": [],
    }
    schema = {
        "data": [
            {"name": "short_description", "attribute_type": "normal", "is_mandatory": 1},
            {
                "name": "short_description_en",
                "attribute_type": "normal",
                "is_mandatory": 1,
            },
            {"name": "brand", "attribute_type": "normal", "is_mandatory": 1},
            {"name": "name", "attribute_type": "normal", "is_mandatory": 1},
            {"name": "SellerSku", "attribute_type": "sku", "is_mandatory": 1},
            {"name": "price", "attribute_type": "sku", "is_mandatory": 1},
            {
                "name": "color_family",
                "attribute_type": "sku",
                "is_mandatory": 1,
                "options": [{"name": "Black"}],
            },
        ]
    }
    create_calls = {"n": 0}

    def boom(*_a, **_k):
        create_calls["n"] += 1
        raise DarazApiError("timeout", code="TIMEOUT", http_status=None)

    from src.image_migrate import ImageMigrationResult

    with patch("src.product_add.fetch_public_product", return_value=fake):
        with patch("src.product_add.find_connected_owner_for_item", return_value=None):
            with patch("src.product_add.client_for_store") as client_fn:
                mock_client = MagicMock()
                mock_client.get_category_attributes.return_value = schema
                mock_client.query_category_brands.return_value = {
                    "data": {"module": [{"name": "No Brand", "brand_id": 1}]}
                }
                mock_client.create_product.side_effect = boom
                client_fn.return_value = mock_client
                with patch(
                    "src.image_migrate.DarazImageMigrationService.resolve_many",
                    return_value=[
                        ImageMigrationResult(
                            source_url="https://static-01.daraz.pk/p/a.jpg",
                            status="completed",
                            migrated_url="https://static-01.daraz.pk/p/a.jpg",
                            strategy="reuse_cdn",
                        )
                    ],
                ):
                    first = add_product_from_public_url(
                        wid,
                        "https://www.daraz.pk/products/x-i99.html",
                        ["store-a"],
                        execute=True,
                    )
                    second = add_product_from_public_url(
                        wid,
                        "https://www.daraz.pk/products/x-i99.html",
                        ["store-a"],
                        execute=True,
                    )

    assert create_calls["n"] == 1
    assert first["destinations"][0]["status"] == "Failed"
    attempt_id = first["destinations"][0].get("attempt_id")
    assert attempt_id
    att = tenancy_env.get_product_attempt(wid, attempt_id)
    assert att["state"] == "NEEDS_RECONCILIATION"
    assert second["destinations"][0]["creation_status"] == "NEEDS_RECONCILIATION"
    assert "requires_reconciliation" in (second["destinations"][0].get("reason") or "")


def test_reconcile_by_destination_item_id(tenancy_env):
    wid, dest_uuid, _ = _boot(tenancy_env)
    att = _attempt(
        tenancy_env,
        wid,
        dest_uuid,
        state="NEEDS_RECONCILIATION",
        destination_item_id="555",
    )
    product = {
        "item_id": "555",
        "skus": [
            {"SellerSku": "MTF-101", "SkuId": "d1"},
            {"SellerSku": "MTF-102", "SkuId": "d2"},
        ],
    }
    with patch("src.product_create_reconcile._client_for_destination") as cf:
        mock = MagicMock()
        mock.get_product_item.return_value = {"code": "0", "data": product}
        cf.return_value = mock
        result = reconcile_product_create_attempt(wid, att["id"], actor_user_id="u")
    assert result["status"] == "VERIFIED"
    refreshed = tenancy_env.get_product_attempt(wid, att["id"])
    assert refreshed["state"] == "VERIFIED"
    assert refreshed["destination_item_id"] == "555"
    assert refreshed["destination_sku_mapping"]["101"]["destination_seller_sku"] == "MTF-101"
    assert refreshed["destination_sku_mapping"]["101"]["destination_daraz_sku_id"] == "d1"


def test_reconcile_by_exact_seller_sku(tenancy_env):
    wid, dest_uuid, _ = _boot(tenancy_env)
    att = _attempt(tenancy_env, wid, dest_uuid, state="CREATED_UNVERIFIED")
    product = {
        "item_id": "777",
        "skus": [
            {"SellerSku": "MTF-101", "SkuId": "x1"},
            {"SellerSku": "MTF-102", "SkuId": "x2"},
        ],
    }
    with patch("src.product_create_reconcile._client_for_destination") as cf:
        mock = MagicMock()
        mock.get_products.return_value = {
            "code": "0",
            "data": {"products": [product]},
        }
        cf.return_value = mock
        result = reconcile_product_create_attempt(wid, att["id"])
    assert result["status"] == "VERIFIED"
    assert result["destination_item_id"] == "777"


def test_reconcile_conclusively_not_found(tenancy_env):
    wid, dest_uuid, _ = _boot(tenancy_env)
    att = _attempt(tenancy_env, wid, dest_uuid)
    with patch("src.product_create_reconcile._client_for_destination") as cf:
        mock = MagicMock()
        mock.get_products.return_value = {"code": "0", "data": {"products": []}}
        cf.return_value = mock
        result = reconcile_product_create_attempt(wid, att["id"])
    assert result["status"] == "FAILED_SAFE_TO_RETRY"
    assert tenancy_env.get_product_attempt(wid, att["id"])["state"] == "FAILED_SAFE_TO_RETRY"


def test_reconcile_uncertain_lookup(tenancy_env):
    wid, dest_uuid, _ = _boot(tenancy_env)
    att = _attempt(tenancy_env, wid, dest_uuid)
    with patch("src.product_create_reconcile._client_for_destination") as cf:
        mock = MagicMock()
        mock.get_products.side_effect = DarazApiError("boom", code="E500", http_status=500)
        cf.return_value = mock
        result = reconcile_product_create_attempt(wid, att["id"])
    assert result["status"] == "NEEDS_RECONCILIATION"


def test_retry_reuses_seller_skus(tenancy_env):
    wid, dest_uuid, _ = _boot(tenancy_env)
    skus = [
        {"seller_sku": "MTF-KEEP-1", "source_daraz_sku_id": "101"},
        {"seller_sku": "MTF-KEEP-2", "source_daraz_sku_id": "102"},
    ]
    att = _attempt(
        tenancy_env,
        wid,
        dest_uuid,
        state="FAILED_SAFE_TO_RETRY",
        generated_seller_skus=skus,
        request_fingerprint="fp-retry",
    )
    captured: dict[str, Any] = {}

    def fake_add(*_a, **kwargs):
        captured["forced_seller_skus"] = kwargs.get("forced_seller_skus")
        captured["forced_attempt_id"] = kwargs.get("forced_attempt_id")
        tenancy_env.update_product_attempt(
            wid, att["id"], state="VERIFIED", verification_state="VERIFIED"
        )
        return {
            "status": "Created",
            "destinations": [{"status": "Created", "attempt_id": att["id"]}],
        }

    with patch(
        "src.product_add.add_product_from_public_url",
        side_effect=fake_add,
    ):
        result = retry_product_create_attempt(wid, att["id"], execute=True)
    assert result["create_product_called"] is True
    assert captured["forced_attempt_id"] == att["id"]
    forced = captured["forced_seller_skus"]
    assert [x["seller_sku"] for x in forced] == ["MTF-KEEP-1", "MTF-KEEP-2"]


def test_retry_blocked_from_needs_reconciliation(tenancy_env):
    wid, dest_uuid, _ = _boot(tenancy_env)
    att = _attempt(tenancy_env, wid, dest_uuid, state="NEEDS_RECONCILIATION")
    with patch("src.product_add.add_product_from_public_url") as add:
        result = retry_product_create_attempt(wid, att["id"])
    assert result["status"] == "BLOCKED"
    assert result["create_product_called"] is False
    add.assert_not_called()


def test_retry_verified_no_create(tenancy_env):
    wid, dest_uuid, _ = _boot(tenancy_env)
    att = _attempt(tenancy_env, wid, dest_uuid, state="VERIFIED")
    with patch("src.product_add.add_product_from_public_url") as add:
        result = retry_product_create_attempt(wid, att["id"])
    assert result["status"] == "VERIFIED"
    assert result["create_product_called"] is False
    add.assert_not_called()


def test_independent_attempts_per_destination(tenancy_env):
    wid, dest_a, _ = _boot(tenancy_env)
    store_b = tenancy_env.upsert_store(
        wid,
        {
            "store_id": "store-b",
            "account": "b@x.com",
            "seller_id": "s-b",
            "display_name": "Store B",
            "access_token": "t",
            "refresh_token": "r",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )
    a1 = _attempt(
        tenancy_env,
        wid,
        dest_a,
        request_fingerprint="fp-a",
        state="NEEDS_RECONCILIATION",
    )
    a2 = _attempt(
        tenancy_env,
        wid,
        str(store_b["id"]),
        request_fingerprint="fp-b",
        state="FAILED_SAFE_TO_RETRY",
    )
    with patch("src.product_create_reconcile._client_for_destination") as cf:
        mock = MagicMock()
        mock.get_products.return_value = {"code": "0", "data": {"products": []}}
        cf.return_value = mock
        reconcile_product_create_attempt(wid, a1["id"])
    assert tenancy_env.get_product_attempt(wid, a1["id"])["state"] == "FAILED_SAFE_TO_RETRY"
    assert tenancy_env.get_product_attempt(wid, a2["id"])["state"] == "FAILED_SAFE_TO_RETRY"


def test_cross_workspace_attempt_denied(tenancy_env):
    wid1, dest1, client = _boot(tenancy_env, user="u-w1")
    att = _attempt(tenancy_env, wid1, dest1)
    headers2 = {
        "Authorization": f"Bearer {issue_test_token('u-w2', email='w2@x.com')}"
    }
    boot2 = client.post("/api/bootstrap", headers=headers2)
    wid2 = boot2.json()["workspace"]["id"]
    # Attempt belongs to wid1; wid2 must not see it
    assert tenancy_env.get_product_attempt(wid2, att["id"]) is None
    res = client.get(
        f"/api/product-create-attempts/{att['id']}",
        headers={**headers2, "X-Workspace-Id": wid2},
    )
    assert res.status_code == 404


def test_destination_sku_mapping_persisted(tenancy_env):
    wid, dest_uuid, _ = _boot(tenancy_env)
    att = _attempt(tenancy_env, wid, dest_uuid, destination_item_id="9")
    product = {
        "item_id": "9",
        "skus": [{"SellerSku": "MTF-101", "SkuId": "dest-sku-1"}, {"SellerSku": "MTF-102", "SkuId": "dest-sku-2"}],
    }
    with patch("src.product_create_reconcile._client_for_destination") as cf:
        mock = MagicMock()
        mock.get_product_item.return_value = {"code": "0", "data": product}
        cf.return_value = mock
        reconcile_product_create_attempt(wid, att["id"])
    mapping = tenancy_env.get_product_attempt(wid, att["id"])["destination_sku_mapping"]
    assert mapping["102"]["destination_daraz_sku_id"] == "dest-sku-2"
    assert mapping["102"]["source_daraz_sku_id"] == "102"


def test_blocking_validation_skips_image_resolution(tenancy_env):
    from src.product_add import add_product_from_public_url

    wid, _, _ = _boot(tenancy_env)
    fake = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i2.html",
        "item_id": "2",
        "title": "Pack Only",
        "brand": "Nike",
        "description_html": "<p>p</p>",
        "images": ["https://static-01.daraz.pk/p/a.jpg"],
        "category_id": "10001948",
        "category_resolution": {"confidence": "high", "source": "catalog"},
        "variants": [
            {
                "daraz_sku_id": "201",
                "price": 500,
                "sale_props": {"Pack": "Pack of 1"},
                "dimensions": [
                    {
                        "source_property_name": "Pack",
                        "source_value": "Pack of 1",
                        "semantic_type": "pack",
                    }
                ],
                "price_source": "mtop.getDetailInfo",
            }
        ],
        "pricing_summary": {
            "regular_prices_complete": True,
            "missing_variant_prices": 0,
        },
        "timings_ms": {},
        "warnings": [],
    }
    schema = {
        "data": [
            {"name": "short_description", "attribute_type": "normal", "is_mandatory": 1},
            {
                "name": "short_description_en",
                "attribute_type": "normal",
                "is_mandatory": 1,
            },
            {"name": "brand", "attribute_type": "normal", "is_mandatory": 1},
            {"name": "name", "attribute_type": "normal", "is_mandatory": 1},
            {
                "name": "color_family",
                "attribute_type": "sku",
                "is_mandatory": 1,
                "options": [{"name": "Black"}],
            },
        ]
    }
    with patch("src.product_add.fetch_public_product", return_value=fake):
        with patch("src.product_add.find_connected_owner_for_item", return_value=None):
            with patch("src.product_add.client_for_store") as client_fn:
                mock_client = MagicMock()
                mock_client.get_category_attributes.return_value = schema
                mock_client.query_category_brands.return_value = {
                    "data": {"module": [{"name": "No Brand", "brand_id": 1}]}
                }
                client_fn.return_value = mock_client
                with patch(
                    "src.image_migrate.DarazImageMigrationService.resolve_many"
                ) as resolve_many:
                    result = add_product_from_public_url(
                        wid,
                        "https://www.daraz.pk/products/x-i2.html",
                        ["store-a"],
                        execute=False,
                    )
    resolve_many.assert_not_called()
    assert result["needs_attention_count"] == 1
    strategy = (
        (result["destinations"][0].get("draft_preview") or {})
        .get("image_strategy")
        or (result["destinations"][0].get("validation") or {})
    )
    # skipped path recorded on draft media
    assert result["destinations"][0]["status"] == "NEEDS_ATTENTION"


def test_audit_events_no_sensitive_fields(tenancy_env):
    wid, dest_uuid, _ = _boot(tenancy_env)
    att = _attempt(tenancy_env, wid, dest_uuid)
    with patch("src.product_create_reconcile._client_for_destination") as cf:
        mock = MagicMock()
        mock.get_products.return_value = {"code": "0", "data": {"products": []}}
        cf.return_value = mock
        reconcile_product_create_attempt(wid, att["id"], actor_user_id="actor-1")
    events = tenancy_env.list_audit_events(wid)
    if isinstance(events, dict):
        events = events.get("items") or []
    assert events
    for ev in events:
        meta = ev.get("metadata") or {}
        assert "access_token" not in meta
        assert "refresh_token" not in meta
        assert "authorization" not in meta
        assert "payload" not in meta
    cleaned = safe_metadata(
        {"access_token": "x", "attempt_id": "1", "authorization": "Bearer x"}
    )
    assert cleaned == {"attempt_id": "1"}


def test_apply_attempt_seller_skus_by_source_id():
    draft = {
        "variants": [
            {"daraz_sku_id": "102", "seller_sku": "NEW-2"},
            {"daraz_sku_id": "101", "seller_sku": "NEW-1"},
        ]
    }
    report = apply_attempt_seller_skus(
        draft,
        [
            {"seller_sku": "MTF-101", "source_daraz_sku_id": "101"},
            {"seller_sku": "MTF-102", "source_daraz_sku_id": "102"},
        ],
    )
    assert report["mode"] == "by_source_sku_id"
    by_id = {v["daraz_sku_id"]: v["seller_sku"] for v in draft["variants"]}
    assert by_id == {"101": "MTF-101", "102": "MTF-102"}


def test_api_reconcile_and_retry_capabilities(tenancy_env):
    wid, dest_uuid, client = _boot(tenancy_env)
    att = _attempt(tenancy_env, wid, dest_uuid, state="FAILED_SAFE_TO_RETRY")
    headers = {
        "Authorization": f"Bearer {issue_test_token('u-b1', email='u-b1@x.com')}",
        "X-Workspace-Id": wid,
    }
    with patch("src.product_create_reconcile._client_for_destination") as cf:
        mock = MagicMock()
        mock.get_products.return_value = {"code": "0", "data": {"products": []}}
        cf.return_value = mock
        # already FAILED_SAFE_TO_RETRY — reconcile still runs
        att2 = _attempt(
            tenancy_env,
            wid,
            dest_uuid,
            state="NEEDS_RECONCILIATION",
            request_fingerprint="fp-api",
        )
        res = client.post(
            f"/api/product-create-attempts/{att2['id']}/reconcile",
            headers=headers,
        )
    assert res.status_code == 200
    assert res.json()["status"] == "FAILED_SAFE_TO_RETRY"

    with patch(
        "src.product_add.add_product_from_public_url",
        return_value={"destinations": []},
    ):
        res2 = client.post(
            f"/api/product-create-attempts/{att['id']}/retry",
            headers=headers,
        )
    assert res2.status_code == 200
    assert res2.json()["create_product_called"] is True
