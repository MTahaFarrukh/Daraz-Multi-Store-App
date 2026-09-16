"""Public Daraz URL import — SSRF, validation, package defaults, MTF SKUs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.public_daraz import (
    PublicDarazError,
    build_public_clone_draft,
    clear_public_cache_for_tests,
    normalize_daraz_product_url,
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
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_public_cache_for_tests()
    return reset_repo_for_tests()


def test_normalize_valid_and_tracking(monkeypatch):
    monkeypatch.setattr("src.public_daraz._resolve_public", lambda host: None)
    clean, item = normalize_daraz_product_url(
        "https://www.daraz.pk/products/foo-i1974026524.html?spm=a&scm=x"
    )
    assert item == "1974026524"
    assert "spm" not in clean
    assert clean.startswith("https://www.daraz.pk/")


def test_reject_non_daraz_and_localhost(monkeypatch):
    monkeypatch.setattr("src.public_daraz._resolve_public", lambda host: None)
    with pytest.raises(PublicDarazError, match="daraz.pk"):
        normalize_daraz_product_url("https://evil.example/p/i123456.html")
    with pytest.raises(PublicDarazError):
        normalize_daraz_product_url("http://www.daraz.pk/products/foo-i1974026524.html")
    with pytest.raises(PublicDarazError):
        normalize_daraz_product_url("https://localhost/products/foo-i1974026524.html")


def test_reject_non_product_path(monkeypatch):
    monkeypatch.setattr("src.public_daraz._resolve_public", lambda host: None)
    with pytest.raises(PublicDarazError, match="product"):
        normalize_daraz_product_url("https://www.daraz.pk/wow/campaign/")


def test_public_draft_uses_defaults_and_mtf(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    tenancy_env.upsert_store(
        wid,
        {
            "store_id": "store_b",
            "account": "b@x.com",
            "seller_id": "s",
            "display_name": "B",
            "access_token": "t",
            "refresh_token": "r",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )
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
        "source_url": "https://www.daraz.pk/products/x-i1974026524.html",
        "item_id": "1974026524",
        "title": "Public Glow Toy",
        "brand": "No Brand",
        "description_html": "<p>Hello</p><script>x</script>",
        "images": ["https://static-01.daraz.pk/p/a.png"],
        "price": 199.0,
        "category_hint": "Toys",
        "variants": [],
        "provenance": {},
        "timings_ms": {"fetch_extract": 1},
        "warnings": [],
    }
    with patch("src.public_daraz.fetch_public_product", return_value=fake):
        result = build_public_clone_draft(
            wid,
            url="https://www.daraz.pk/products/x-i1974026524.html",
            destination_store_id="store_b",
        )
    draft = result["draft"]
    assert draft["source_type"] == "public_daraz_url"
    assert draft["variants"][0]["seller_sku"].startswith("MTF-")
    assert draft["variants"][0]["quantity"] == 1
    assert draft["variants"][0]["package_weight"] == 0.5
    assert "script" not in (draft["product"]["description_html"] or "").lower()
    assert draft["validation"]["create_gated"] is True


def test_public_draft_blocks_without_package_defaults(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    tenancy_env.upsert_store(
        wid,
        {
            "store_id": "store_b",
            "account": "b@x.com",
            "seller_id": "s",
            "display_name": "B",
            "access_token": "t",
            "refresh_token": "r",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )
    fake = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i1974026524.html",
        "item_id": "1974026524",
        "title": "Public Glow Toy",
        "brand": None,
        "description_html": "<p>Hi</p>",
        "images": ["https://static-01.daraz.pk/p/a.png"],
        "price": 10,
        "category_hint": None,
        "variants": [],
        "provenance": {},
        "timings_ms": {},
        "warnings": [],
    }
    with patch("src.public_daraz.fetch_public_product", return_value=fake):
        result = build_public_clone_draft(
            wid,
            url="https://www.daraz.pk/products/x-i1974026524.html",
            destination_store_id="store_b",
        )
    assert result["draft"]["validation"]["errors"]
    assert result["draft"]["validation"]["can_create"] is False
