"""URL / single-product add — mapping + claim gate when connected identity is known."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.product_add import _prepare_destination, add_product_from_public_url
from src.product_copy_mapping import (
    acquire_create_claim,
    gate_create_with_mapping_and_claim,
    record_verified_copy_mapping,
    resolve_connected_copy_identity,
)
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
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "true")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_public_cache_for_tests()
    return reset_repo_for_tests()


def _store(repo, wid: str, store_id: str) -> dict[str, Any]:
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


def _draft(src: dict[str, Any], item_id: str = "555") -> dict[str, Any]:
    return {
        "source_type": "connected_store",
        "source_store_uuid": src["id"],
        "source_daraz_item_id": item_id,
        "product": {"title": "Glow Toy", "primary_category_id": "100"},
        "variants": [
            {
                "seller_sku": "MTF-1",
                "price": 100.0,
                "quantity": 1,
                "package_weight": 0.5,
                "package_length": 10,
                "package_width": 8,
                "package_height": 4,
                "sale_props": {"Color": "Red"},
            }
        ],
        "media": {"product_images": ["https://static-01.daraz.pk/p/a.png"]},
        "duplicate_check_done": True,
        "possible_duplicates": [],
        "category_resolution": {"confidence": "high", "conflict": False},
    }


def test_resolve_identity_skips_pure_public_url(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    assert (
        resolve_connected_copy_identity(
            wid,
            {
                "source_type": "public_daraz_url",
                "source_daraz_item_id": "999",
                "source_store_id": "store_a",
            },
        )
        is None
    )


def test_resolve_identity_connected_via_public_url(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _store(tenancy_env, wid, "store_a")
    ident = resolve_connected_copy_identity(
        wid,
        {
            "source_type": "connected_via_public_url",
            "source_store_uuid": src["id"],
            "source_daraz_item_id": "1974026524",
        },
    )
    assert ident is not None
    assert ident["source_store_id"] == src["id"]
    assert ident["source_daraz_item_id"] == "1974026524"


def test_gate_skips_when_verified_mapping_exists(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _store(tenancy_env, wid, "store_a")
    dest = _store(tenancy_env, wid, "store_b")
    record_verified_copy_mapping(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="111",
        destination_store_id=dest["id"],
        destination_item_id="999",
    )
    gated = gate_create_with_mapping_and_claim(
        wid,
        draft={
            "source_type": "connected_store",
            "source_store_uuid": src["id"],
            "source_daraz_item_id": "111",
        },
        destination_store=dest,
    )
    assert gated["action"] == "skip_verified"
    assert gated["result"]["reason"] == "already_copied_to_destination"
    assert gated["result"]["item_id"] == "999"


def test_gate_blocks_held_claim(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _store(tenancy_env, wid, "store_a")
    dest = _store(tenancy_env, wid, "store_b")
    first = acquire_create_claim(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="222",
        destination_store_id=dest["id"],
    )
    assert first["acquired"] is True
    gated = gate_create_with_mapping_and_claim(
        wid,
        draft={
            "source_type": "connected_store",
            "source_store_uuid": src["id"],
            "source_daraz_item_id": "222",
        },
        destination_store=dest,
    )
    assert gated["action"] == "block"
    assert gated["result"]["reason"] == "concurrent_create_claim_held"


def test_prepare_skips_create_when_mapping_verified(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _store(tenancy_env, wid, "store_a")
    dest = _store(tenancy_env, wid, "store_b")
    record_verified_copy_mapping(
        wid,
        source_store_id=src["id"],
        source_daraz_item_id="555",
        destination_store_id=dest["id"],
        destination_item_id="888",
    )
    mock_client = MagicMock()
    with patch("src.product_add.client_for_store", return_value=mock_client):
        out = _prepare_destination(
            wid,
            draft=_draft(src, "555"),
            dest=dest,
            price_override=None,
            allow_duplicates=False,
            execute=True,
            confirm=True,
            gate_on=True,
            claim_gate=True,
        )
    assert out["reason"] == "already_copied_to_destination"
    assert out["item_id"] == "888"
    assert out["creation_status"] == "VERIFIED"
    mock_client.create_product.assert_not_called()
    mock_client.get_category_attributes.assert_not_called()


def test_prepare_blocks_when_uncertain_attempt(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _store(tenancy_env, wid, "store_a")
    dest = _store(tenancy_env, wid, "store_b")
    tenancy_env.create_product_attempt(
        {
            "workspace_id": wid,
            "source_type": "connected_store",
            "source_identity": f"connected:{src['id']}:666",
            "destination_store_id": dest["id"],
            "request_fingerprint": "fp-uncertain-666",
            "state": "CREATED_UNVERIFIED",
            "generated_seller_skus": [{"seller_sku": "MTF-1"}],
            "verification_state": "UNVERIFIED",
            "destination_item_id": None,
        }
    )
    out = _prepare_destination(
        wid,
        draft=_draft(src, "666"),
        dest=dest,
        price_override=None,
        allow_duplicates=False,
        execute=True,
        confirm=True,
        gate_on=True,
        claim_gate=True,
    )
    assert out["creation_status"] == "NEEDS_RECONCILIATION"
    assert out["reason"] == "product_create_attempt_requires_reconciliation"


def test_public_url_without_owner_does_not_claim(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    dest = _store(tenancy_env, wid, "store_b")
    tenancy_env.upsert_product_defaults(
        wid,
        {
            "default_package_weight": 0.5,
            "default_package_length": 10,
            "default_package_width": 8,
            "default_package_height": 4,
            "default_initial_quantity": 1,
        },
    )
    fake = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i123456.html",
        "item_id": "123456",
        "title": "Public Toy",
        "brand": "No Brand",
        "description_html": "<p>Hi</p>",
        "images": ["https://static-01.daraz.pk/p/a.png"],
        "price": 100.0,
        "category_hint": "Toys",
        "category_id": "100",
        "variants": [{"price": 100.0, "sale_props": {"Color": "Red"}}],
        "provenance": {},
        "timings_ms": {"fetch_extract": 1},
        "warnings": [],
        "extraction_complete": True,
        "pricing_summary": {"regular_prices_complete": True},
        "category_resolution": {
            "category_id": "100",
            "confidence": "high",
            "conflict": False,
        },
    }
    claimed: list[int] = []

    def _track(*_a, **_k):
        claimed.append(1)
        return {"acquired": True, "claim": {"id": "c"}, "owner_token": "t"}

    monkeypatch.setattr("src.product_copy_mapping.acquire_create_claim", _track)

    with patch("src.product_add.fetch_public_product", return_value=fake):
        with patch("src.product_add.find_connected_owner_for_item", return_value=None):
            with patch("src.product_add.client_for_store", return_value=MagicMock()):
                result = add_product_from_public_url(
                    wid,
                    "https://www.daraz.pk/products/x-i123456.html",
                    [dest["store_id"]],
                    execute=True,
                    confirm=True,
                )
    assert claimed == []
    assert result.get("source", {}).get("resolution") == "public_daraz_url"


def test_ssrf_redirect_rejected(monkeypatch):
    from src.public_daraz import PublicDarazError, normalize_daraz_product_url

    monkeypatch.setattr("src.public_daraz._resolve_public", lambda host: None)
    with pytest.raises(PublicDarazError):
        normalize_daraz_product_url("https://evil.example/products/foo-i123456.html")
    with pytest.raises(PublicDarazError):
        normalize_daraz_product_url("http://www.daraz.pk/products/foo-i123456.html")
    with pytest.raises(PublicDarazError):
        normalize_daraz_product_url("https://127.0.0.1/products/foo-i123456.html")


def test_public_url_verified_attempt_blocks_recreate_despite_sku_fingerprint(
    tenancy_env,
):
    """Same source URL→dest must not CreateProduct again when attempt is verified.

    Fingerprints include Seller SKUs; SKUs can change between runs, so identity
    must be checked independently of the fingerprint.
    """
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    dest = _store(tenancy_env, wid, "store_b")
    source_url = "https://www.daraz.pk/products/x-i123456.html"
    tenancy_env.create_product_attempt(
        {
            "workspace_id": wid,
            "source_type": "public_daraz_url",
            "source_identity": source_url,
            "destination_store_id": dest["id"],
            "request_fingerprint": "fp-old-skus-different",
            "state": "VERIFIED",
            "generated_seller_skus": [{"seller_sku": "MTF-OLD"}],
            "verification_state": "VERIFIED",
            "destination_item_id": "777001",
        }
    )
    draft = {
        "source_type": "public_daraz_url",
        "source_url": source_url,
        "source_daraz_item_id": "123456",
        "product": {"title": "Public Toy", "primary_category_id": "100"},
        "variants": [
            {
                "seller_sku": "MTF-NEW-DIFFERENT",
                "price": 100.0,
                "quantity": 1,
                "package_weight": 0.5,
                "package_length": 10,
                "package_width": 8,
                "package_height": 4,
                "sale_props": {"Color": "Red"},
            }
        ],
        "media": {"product_images": ["https://static-01.daraz.pk/p/a.png"]},
        "duplicate_check_done": True,
        "possible_duplicates": [],
        "category_resolution": {"confidence": "high", "conflict": False},
    }
    mock_client = MagicMock()
    with patch("src.product_add.client_for_store", return_value=mock_client):
        out = _prepare_destination(
            wid,
            draft=draft,
            dest=dest,
            price_override=None,
            allow_duplicates=False,
            execute=True,
            confirm=True,
            gate_on=True,
            claim_gate=True,
        )
    assert out["reason"] == "product_create_attempt_already_verified"
    assert out["item_id"] == "777001"
    mock_client.create_product.assert_not_called()
