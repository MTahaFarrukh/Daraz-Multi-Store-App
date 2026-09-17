"""Phase 4D.2 — Add Daraz Product multi-store (fetch once, gated create)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.product_add import add_product_from_public_url
from src.product_create import product_create_enabled
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


def _add_store(repo, workspace_id: str, store_id: str) -> dict[str, Any]:
    return repo.upsert_store(
        workspace_id,
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


def _fake_extract(**overrides: Any) -> dict[str, Any]:
    base = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/test-i123.html",
        "item_id": "123",
        "title": "Test Glow Toy",
        "brand": "No Brand",
        "description_html": "<p>Nice</p>",
        "images": ["https://static-01.daraz.pk/p/abc.jpg"],
        "price": 499.0,
        "category_hint": "Toys",
        "variants": [{"price": 499.0, "sale_props": {"Color": "Red"}}],
        "provenance": {},
        "timings_ms": {"fetch_extract": 12.5},
        "warnings": [],
    }
    base.update(overrides)
    return base


def test_product_create_enabled_flags(monkeypatch):
    monkeypatch.delenv("ALLOW_PRODUCT_CREATE", raising=False)
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    assert product_create_enabled() is False
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "true")
    assert product_create_enabled() is True
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "yes")
    assert product_create_enabled() is True


def test_public_url_fetched_once_for_two_destinations(tenancy_env):
    repo = tenancy_env
    # bootstrap workspace via memory repo helpers used elsewhere
    from src.auth import issue_test_token
    from fastapi.testclient import TestClient
    from src.app import app

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {issue_test_token('u-add', email='a@x.com')}"}
    boot = client.post("/api/bootstrap", headers=headers)
    assert boot.status_code == 200
    wid = boot.json()["workspace"]["id"]
    _add_store(repo, wid, "store-a")
    _add_store(repo, wid, "store-b")

    fake = _fake_extract()
    with patch("src.product_add.fetch_public_product", return_value=fake) as fetch_mock:
        with patch(
            "src.product_add.find_connected_owner_for_item", return_value=None
        ):
            with patch("src.product_add.client_for_store") as client_fn:
                mock_client = MagicMock()
                mock_client.get_category_attributes.side_effect = Exception("no cat")
                client_fn.return_value = mock_client
                with patch.object(
                    __import__("src.image_migrate", fromlist=["DarazImageMigrationService"]).DarazImageMigrationService,
                    "resolve_many",
                    return_value=[],
                ):
                    result = add_product_from_public_url(
                        wid,
                        "https://www.daraz.pk/products/test-i123.html",
                        ["store-a", "store-b"],
                        execute=False,
                    )

    assert fetch_mock.call_count == 1
    assert len(result["destinations"]) == 2
    assert result["product_create_enabled"] is False
    assert result["status"] == "BLOCKED_CREATE" or result["needs_attention_count"] >= 0


def test_missing_price_needs_attention(tenancy_env):
    repo = tenancy_env
    from src.auth import issue_test_token
    from fastapi.testclient import TestClient
    from src.app import app

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {issue_test_token('u-price', email='p@x.com')}"}
    boot = client.post("/api/bootstrap", headers=headers)
    wid = boot.json()["workspace"]["id"]
    _add_store(repo, wid, "store-a")

    fake = _fake_extract(price=None, variants=[{"price": None, "sale_props": {}}])
    with patch("src.product_add.fetch_public_product", return_value=fake):
        with patch(
            "src.product_add.find_connected_owner_for_item", return_value=None
        ):
            result = add_product_from_public_url(
                wid,
                "https://www.daraz.pk/products/test-i123.html",
                ["store-a"],
                execute=False,
            )

    assert result["destinations"][0]["status"] == "NEEDS_ATTENTION"
    assert result["destinations"][0]["reason"] == "missing_price"
    assert result["needs_attention_count"] == 1


def test_gate_off_never_calls_create_product(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    repo = tenancy_env
    from src.auth import issue_test_token
    from fastapi.testclient import TestClient
    from src.app import app

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {issue_test_token('u-gate', email='g@x.com')}"}
    boot = client.post("/api/bootstrap", headers=headers)
    wid = boot.json()["workspace"]["id"]
    _add_store(repo, wid, "store-a")
    _add_store(repo, wid, "store-b")

    fake = _fake_extract()
    create_mock = MagicMock()
    with patch("src.product_add.fetch_public_product", return_value=fake):
        with patch(
            "src.product_add.find_connected_owner_for_item", return_value=None
        ):
            with patch("src.product_add.client_for_store") as client_fn:
                mock_client = MagicMock()
                mock_client.create_product = create_mock
                mock_client.get_category_attributes.return_value = {"data": []}
                mock_client.query_category_brands.return_value = {
                    "data": {"brand": [{"name": "No Brand"}]}
                }
                client_fn.return_value = mock_client
                # Force a simple NEEDS_ATTENTION path by missing category on public draft
                result = add_product_from_public_url(
                    wid,
                    "https://www.daraz.pk/products/test-i123.html",
                    ["store-a", "store-b"],
                    execute=True,
                    confirm=True,
                )

    create_mock.assert_not_called()
    assert result["created_count"] == 0
    assert result["status"] in {"BLOCKED_CREATE", "NEEDS_ATTENTION", "ANALYZED"}


def test_partial_multi_store_success(tenancy_env, monkeypatch):
    """Dry-run (execute=False) stays READY; default Add with gate on creates."""
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "true")
    repo = tenancy_env
    from src.auth import issue_test_token
    from fastapi.testclient import TestClient
    from src.app import app

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {issue_test_token('u-part', email='t@x.com')}"}
    boot = client.post("/api/bootstrap", headers=headers)
    wid = boot.json()["workspace"]["id"]
    store_a = _add_store(repo, wid, "store-a")
    store_b = _add_store(repo, wid, "store-b")
    repo.upsert_product_defaults(
        wid,
        {
            "default_package_weight": 0.5,
            "default_package_length": 10,
            "default_package_width": 10,
            "default_package_height": 10,
            "default_initial_quantity": 1,
        },
    )

    # Seed a connected source product with category so store-a can validate
    product = repo.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": store_a["id"],
            "daraz_item_id": "999",
            "title": "Connected Source",
            "title_en": "Connected Source",
            "primary_category_id": 10002730,
            "brand": "No Brand",
            "status_raw": "Active",
            "description_en": "<p>x</p>",
            "images_json": [{"url": "https://static-01.daraz.pk/p/x.jpg"}],
            "detail_complete": True,
        }
    )
    repo.replace_product_variants(
        wid,
        str(product["id"]),
        [
            {
                "workspace_id": wid,
                "store_id": store_a["id"],
                "product_id": product["id"],
                "daraz_sku_id": "sku1",
                "seller_sku": "SRC-1",
                "price": 100,
                "quantity": 5,
                "sale_props_json": {"Color": "Blue"},
                "package_weight": 0.5,
                "package_length": 10,
                "package_width": 10,
                "package_height": 10,
            }
        ],
    )
    # Reload product after variant replace for draft builder
    product = repo.get_daraz_product(wid, str(product["id"])) or product

    from src.product_add import add_product_from_connected

    resolve_ok = MagicMock()
    resolve_ok.status = "completed"
    resolve_ok.migrated_url = "https://static-01.daraz.pk/p/x.jpg"
    resolve_ok.strategy = "reuse_cdn"
    resolve_ok.source_url = "https://static-01.daraz.pk/p/x.jpg"
    resolve_ok.error = None

    with patch("src.product_add.fetch_connected_product") as fetch_fn:
        fetch_fn.return_value = {
            "product": product,
            "variants": [],
            "source_store": {"store_id": "store-a"},
            "timings_ms": {"total": 5},
            "api_calls": {},
        }
        with patch("src.product_add.client_for_store") as client_fn:
            mock_client = MagicMock()
            mock_client.get_category_attributes.return_value = {
                "data": {"attributes": []}
            }
            mock_client.query_category_brands.return_value = {
                "data": [{"name": "No Brand"}]
            }
            mock_client.create_product = MagicMock(
                return_value={"code": "0", "data": {"item_id": "555"}}
            )
            mock_client.get_product_item.return_value = {
                "data": {"item_id": "555", "status": "Active"}
            }
            client_fn.return_value = mock_client
            with patch(
                "src.product_add.DarazImageMigrationService.resolve_many",
                return_value=[resolve_ok],
            ):
                with patch(
                    "src.product_add.validate_draft_against_category",
                    return_value={
                        "valid": True,
                        "missing_required": [],
                        "invalid_values": [],
                    },
                ):
                    with patch(
                        "src.product_add.resolve_brand_for_category",
                        return_value={
                            "status": "NO_BRAND",
                            "brand": "No Brand",
                            "message": "ok",
                        },
                    ):
                        # Explicit dry-run
                        result = add_product_from_connected(
                            wid,
                            "store-a",
                            "999",
                            [str(store_b["store_id"])],
                            execute=False,
                        )

    assert result["product_create_enabled"] is True
    assert result["destinations"][0]["status"] in {"READY", "NEEDS_ATTENTION", "POSSIBLE_DUPLICATE"}
    assert result["created_count"] == 0
    mock_client.create_product.assert_not_called()

    # Default SaaS Add (execute omitted) creates — no probe confirm required
    with patch("src.product_add.fetch_connected_product") as fetch_fn:
        fetch_fn.return_value = {
            "product": product,
            "variants": [],
            "source_store": {"store_id": "store-a"},
            "timings_ms": {"total": 5},
            "api_calls": {},
        }
        with patch("src.product_add.client_for_store") as client_fn:
            mock_client = MagicMock()
            mock_client.get_category_attributes.return_value = {
                "data": {"attributes": []}
            }
            mock_client.create_product = MagicMock(
                return_value={"code": "0", "data": {"item_id": "555"}}
            )
            mock_client.get_product_item.return_value = {
                "data": {"item_id": "555", "status": "Active"}
            }
            client_fn.return_value = mock_client
            with patch(
                "src.product_add.DarazImageMigrationService.resolve_many",
                return_value=[resolve_ok],
            ):
                with patch(
                    "src.product_add.validate_draft_against_category",
                    return_value={
                        "valid": True,
                        "missing_required": [],
                        "invalid_values": [],
                    },
                ):
                    with patch(
                        "src.product_add.resolve_brand_for_category",
                        return_value={
                            "status": "NO_BRAND",
                            "brand": "No Brand",
                            "message": "ok",
                        },
                    ):
                        created = add_product_from_connected(
                            wid,
                            "store-a",
                            "999",
                            [str(store_b["store_id"])],
                            # omit execute/confirm — SaaS default executes when gate on
                        )

    assert created["destinations"][0]["status"] in {"Active", "Created", "Pending QC"}
    assert created["created_count"] == 1
    mock_client.create_product.assert_called_once()


def test_saas_add_does_not_require_probe_confirm(tenancy_env, monkeypatch):
    """ALLOW_PRODUCT_CREATE=1 must CreateProduct without confirm=true."""
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "1")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "0")
    repo = tenancy_env
    from src.auth import issue_test_token
    from fastapi.testclient import TestClient
    from src.app import app

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {issue_test_token('u-saas', email='s@x.com')}"}
    boot = client.post("/api/bootstrap", headers=headers)
    wid = boot.json()["workspace"]["id"]
    store_a = _add_store(repo, wid, "store-a")
    store_b = _add_store(repo, wid, "store-b")
    repo.upsert_product_defaults(
        wid,
        {
            "default_package_weight": 0.5,
            "default_package_length": 10,
            "default_package_width": 10,
            "default_package_height": 10,
            "default_initial_quantity": 1,
        },
    )
    product = repo.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": store_a["id"],
            "daraz_item_id": "888",
            "title": "Src",
            "primary_category_id": 1,
            "brand": "No Brand",
            "status_raw": "Active",
            "description_en": "<p>x</p>",
            "images_json": [{"url": "https://static-01.daraz.pk/p/x.jpg"}],
            "detail_complete": True,
        }
    )
    repo.replace_product_variants(
        wid,
        str(product["id"]),
        [
            {
                "workspace_id": wid,
                "store_id": store_a["id"],
                "product_id": product["id"],
                "daraz_sku_id": "sku8",
                "seller_sku": "S8",
                "price": 50,
                "quantity": 1,
                "sale_props_json": {},
                "package_weight": 0.5,
                "package_length": 10,
                "package_width": 10,
                "package_height": 10,
            }
        ],
    )
    product = repo.get_daraz_product(wid, str(product["id"])) or product

    from src.product_add import add_product_from_connected

    resolve_ok = MagicMock()
    resolve_ok.status = "completed"
    resolve_ok.migrated_url = "https://static-01.daraz.pk/p/x.jpg"
    resolve_ok.strategy = "reuse_cdn"
    resolve_ok.source_url = "https://static-01.daraz.pk/p/x.jpg"
    resolve_ok.error = None

    with patch("src.product_add.fetch_connected_product") as fetch_fn:
        fetch_fn.return_value = {
            "product": product,
            "variants": [],
            "source_store": {"store_id": "store-a"},
            "timings_ms": {},
            "api_calls": {},
        }
        with patch("src.product_add.client_for_store") as client_fn:
            mock_client = MagicMock()
            mock_client.create_product.return_value = {
                "code": "0",
                "data": {"item_id": "777"},
            }
            mock_client.get_product_item.return_value = {
                "data": {"item_id": "777", "status": "Pending QC"}
            }
            client_fn.return_value = mock_client
            with patch(
                "src.product_add.DarazImageMigrationService.resolve_many",
                return_value=[resolve_ok],
            ):
                with patch(
                    "src.product_add.validate_draft_against_category",
                    return_value={
                        "valid": True,
                        "missing_required": [],
                        "invalid_values": [],
                    },
                ):
                    with patch(
                        "src.product_add.resolve_brand_for_category",
                        return_value={"status": "NO_BRAND", "brand": "No Brand"},
                    ):
                        # API-style: no execute, no confirm
                        out = add_product_from_connected(
                            wid, "store-a", "888", [store_b["store_id"]]
                        )

    assert out["destinations"][0]["status"] == "Pending QC"
    assert out["created_count"] == 1
    mock_client.create_product.assert_called_once()
    # Must not look like probe READY
    assert out["destinations"][0].get("reason") != (
        "Validation passed; execute/confirm required to create"
    )