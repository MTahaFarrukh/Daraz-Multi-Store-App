"""Uniqueness verify deep scan + dry-run payload preview — fakes only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from src.brand_diagnostics import diagnose_category_no_brand
from src.brand_resolve import clear_no_brand_cache_for_tests, deep_scan_no_brand
from src.db import reset_repo_for_tests
from src.product_create_payload import build_create_product_payload_preview
from tests.test_brand_deep_scan_preflight import SyntheticBrandCatalog


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
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_no_brand_cache_for_tests()
    return reset_repo_for_tests()


def test_verify_uniqueness_incomplete_stays_unverified():
    cat = SyntheticBrandCatalog(no_brand_at=100, no_brand_id=39704, total=78630)
    first = deep_scan_no_brand(
        primary_category_id="10000719",
        query_brands=cat.query_category_brands,
        scan_from_start_row=0,
        max_pages=60,
        delay_s=0,
        page_size=200,
        verify_uniqueness=True,
    )
    assert first["status"] == "UNVERIFIED"
    assert first["brand_id"] == 39704
    assert first["uniqueness_verified"] is False
    assert first["lookup"]["complete"] is False
    assert first["lookup"]["candidates_total"] == 1


def test_verify_uniqueness_complete_persists(tenancy_env, monkeypatch):
    repo = tenancy_env
    owner = repo.create_workspace_with_owner("u1", "WS")
    wid = owner["workspace"]["id"]
    store = repo.upsert_store(
        wid,
        {
            "store_id": "mtf",
            "account": "a@x.com",
            "seller_id": "s1",
            "display_name": "MTF",
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 86400,
            "country": "pk",
        },
    )
    cat = SyntheticBrandCatalog(no_brand_at=50, no_brand_id=39704, total=400)

    class Client:
        def query_category_brands(self, **kwargs):
            return cat.query_category_brands(**kwargs)

        def create_product(self, *a, **k):
            raise AssertionError("mutating")

        def migrate_image(self, *a, **k):
            raise AssertionError("mutating")

        def migrate_images(self, *a, **k):
            raise AssertionError("mutating")

    monkeypatch.setattr("src.ops.client_for_store", lambda s: Client())
    monkeypatch.setattr("src.token_refresh.refresh_store_tokens", lambda **k: [])

    out = diagnose_category_no_brand(
        wid,
        store_ref=store["store_id"],
        primary_category_id="10000719",
        deep=True,
        verify_uniqueness=True,
        scan_from_start_row=0,
        prior_brand_id=39704,
    )
    assert out["status"] == "NO_BRAND"
    assert out["uniqueness_verified"] is True
    assert out["complete"] is True
    assert out["candidates_total"] == 1
    assert out.get("persisted") is True
    persisted = repo.get_verified_category_no_brand("pk", "10000719")
    assert persisted
    assert (persisted.get("evidence") or {}).get("uniqueness_verified") is True
    assert (persisted.get("evidence") or {}).get("complete") is True
    assert (persisted.get("evidence") or {}).get("candidates_total") == 1


def test_ambiguous_clears_persisted(tenancy_env, monkeypatch):
    repo = tenancy_env
    owner = repo.create_workspace_with_owner("u1", "WS")
    wid = owner["workspace"]["id"]
    store = repo.upsert_store(
        wid,
        {
            "store_id": "mtf",
            "account": "a@x.com",
            "seller_id": "s1",
            "display_name": "MTF",
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 86400,
            "country": "pk",
        },
    )
    repo.upsert_verified_category_no_brand(
        {
            "marketplace": "pk",
            "primary_category_id": "10000719",
            "brand_name": "No Brand",
            "brand_id": "1",
            "evidence": {
                "complete": True,
                "candidates_total": 1,
                "uniqueness_verified": True,
            },
        }
    )
    cat = SyntheticBrandCatalog(
        no_brand_at=10, no_brand_id=1, ambiguous_extra_id=2, total=400
    )

    class Client:
        def query_category_brands(self, **kwargs):
            return cat.query_category_brands(**kwargs)

    monkeypatch.setattr("src.ops.client_for_store", lambda s: Client())
    monkeypatch.setattr("src.token_refresh.refresh_store_tokens", lambda **k: [])

    out = diagnose_category_no_brand(
        wid,
        store_ref=store["store_id"],
        primary_category_id="10000719",
        deep=True,
        verify_uniqueness=True,
        scan_from_start_row=0,
        prior_brand_id=1,
    )
    assert out["status"] == "UNRESOLVED"
    assert out["reason"] == "ambiguous_no_brand"
    assert repo.get_verified_category_no_brand("pk", "10000719") is None


def test_payload_preview_xml_and_provenance_zero_daraz():
    draft = {
        "product": {
            "title": "Widget",
            "primary_category_id": "10000719",
            "brand": "No Brand",
            "brand_id": 39704,
            "attributes": {},
        },
        "media": {
            "resolved_images": ["https://static-01.daraz.pk/p/a.jpg"],
            "image_strategy": {"strategies": ["reuse_cdn"]},
        },
        "variants": [
            {
                "seller_sku": "MTF-1",
                "price": 199,
                "special_price": 149,
                "quantity": 3,
                "package_weight": "0.2",
                "package_length": "10",
                "package_width": "8",
                "package_height": "4",
                "price_source": "source",
                "package_sources": {
                    "package_weight": "connected_source",
                    "package_length": "workspace_default",
                    "package_width": "workspace_default",
                    "package_height": "workspace_default",
                },
            }
        ],
        "brand_resolution": {
            "status": "NO_BRAND",
            "brand": "No Brand",
            "brand_id": 39704,
            "uniqueness_verified": True,
        },
        "validation": {"can_create": True, "errors": []},
    }
    preview = build_create_product_payload_preview(draft, include_xml=True)
    assert "<brand_id>39704</brand_id>" in (preview.get("create_product_xml") or "")
    assert "<SellerSku>MTF-1</SellerSku>" in preview["create_product_xml"]
    assert preview["Attributes"]["brand_uniqueness_verified"] is True
    fields = {r["field"]: r for r in preview["field_provenance"]}
    assert fields["Attributes.brand_id"]["value"] == 39704
    assert fields["Skus[0].SellerSku"]["origin"] == "generated_mtf"
    assert fields["Skus[0].package_weight"]["origin"] == "connected_source"


def test_dry_run_prepare_blocks_without_uniqueness(tenancy_env, monkeypatch):
    from src.product_add import _prepare_destination

    repo = tenancy_env
    owner = repo.create_workspace_with_owner("u1", "WS")
    wid = owner["workspace"]["id"]
    dest = repo.upsert_store(
        wid,
        {
            "store_id": "dest",
            "account": "d@x.com",
            "seller_id": "sd",
            "display_name": "Dest",
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 86400,
            "country": "pk",
        },
    )

    class Client:
        mutating = []

        def get_category_attributes(self, *a, **k):
            return {"data": []}

        def query_category_brands(self, **kwargs):
            return {
                "data": {
                    "module": [{"name": "No Brand", "brand_id": 39704}],
                    "total_record": 1,
                }
            }

        def get_seller_item_limit(self):
            return {"data": {"onlineItemCount": 1, "itemLimit": 1000}}

        def create_product(self, *a, **k):
            self.mutating.append("create")
            raise AssertionError("no create")

        def migrate_image(self, *a, **k):
            self.mutating.append("migrate")
            raise AssertionError("no migrate")

        def migrate_images(self, *a, **k):
            self.mutating.append("migrate")
            raise AssertionError("no migrate")

        def update_price(self, *a, **k):
            self.mutating.append("price")
            raise AssertionError("no price")

    client = Client()
    monkeypatch.setattr("src.product_add.client_for_store", lambda s: client)
    monkeypatch.setattr(
        "src.product_add.resolve_brand_for_category",
        lambda **k: {
            "status": "NO_BRAND",
            "brand": "No Brand",
            "brand_id": 39704,
            "uniqueness_verified": False,
            "used_no_brand": True,
            "message": "candidate",
        },
    )
    monkeypatch.setattr(
        "src.product_add.get_cached_category_attributes",
        lambda *a, **k: {"data": []},
    )
    monkeypatch.setattr(
        "src.product_add.validate_draft_against_category",
        lambda *a, **k: {"valid": True},
    )
    monkeypatch.setattr(
        "src.product_add.resolve_required_category_attributes",
        lambda *a, **k: {"resolved_count": 0, "unresolved": [], "timings_ms": {}},
    )

    draft = {
        "product": {
            "title": "T",
            "primary_category_id": "10000719",
            "brand": "Acme",
            "attributes": {},
        },
        "media": {"product_images": ["https://static-01.daraz.pk/p/x.jpg"]},
        "variants": [
            {
                "seller_sku": "MTF-X",
                "price": 10,
                "quantity": 1,
                "package_weight": "0.1",
                "package_length": "1",
                "package_width": "1",
                "package_height": "1",
            }
        ],
        "validation": {"errors": []},
        "category_resolution": {"category_id": "10000719"},
    }
    out = _prepare_destination(
        wid,
        draft=draft,
        dest=dest,
        price_override=None,
        allow_duplicates=False,
        execute=False,
        confirm=True,
        gate_on=False,
        allow_image_migrate=False,
    )
    assert client.mutating == []
    assert out["status"] == "NEEDS_ATTENTION"
    assert any("uniqueness_unverified" in str(e) for e in (out.get("validation") or {}).get("errors") or [])
    preview = out.get("draft_preview") or {}
    assert "create_product_xml" in preview
    assert preview.get("field_provenance")
