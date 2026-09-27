"""Phase 4D.5 — public-link fidelity: variants, special price, brand, duplicates."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.brand_resolve import resolve_brand_for_category
from src.db import reset_repo_for_tests
from src.product_create_payload import build_create_product_xml
from src.product_fidelity import (
    apply_price_overrides,
    classify_duplicate_matches,
    parse_money,
    validate_variant_pricing,
)
from src.public_daraz import (
    _apply_price_fallbacks,
    _extract_variants_structured,
    build_public_clone_draft_from_extracted,
    clear_public_cache_for_tests,
)
from src.product_add import add_product_from_public_url, _local_catalog_upsert


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
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_public_cache_for_tests()
    return reset_repo_for_tests()


def _store(repo, wid: str, store_id: str = "store-a") -> dict[str, Any]:
    return repo.upsert_store(
        wid,
        {
            "store_id": store_id,
            "account": f"{store_id}@x.com",
            "seller_sku": "s",
            "seller_id": f"seller-{store_id}",
            "display_name": store_id,
            "access_token": "t",
            "refresh_token": "r",
            "expires_in": 86400,
            "refresh_expires_in": 864000,
            "country": "pk",
        },
    )


def _defaults(repo, wid: str) -> None:
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


# --- pricing helpers ---------------------------------------------------------


def test_parse_money_variants():
    assert parse_money("Rs. 1,000") == 1000.0
    assert parse_money(799) == 799.0
    assert parse_money(None) is None
    assert parse_money("abc") is None


def test_override_single_sku():
    draft = {"variants": [{"seller_sku": "A", "price": None, "sale_props": {}}]}
    report = apply_price_overrides(draft, price_override=500)
    assert report["single_override_applied"] is True
    assert draft["variants"][0]["price"] == 500


def test_override_same_price_variants():
    draft = {
        "variants": [
            {"seller_sku": "A", "price": 1000, "sale_props": {"Color": "Red"}},
            {"seller_sku": "B", "price": 1000, "sale_props": {"Color": "Blue"}},
        ]
    }
    report = apply_price_overrides(draft, price_override=1100)
    assert report["single_override_applied"] is True
    assert draft["variants"][0]["price"] == 1100
    assert draft["variants"][1]["price"] == 1100


def test_override_does_not_flatten_different_prices():
    draft = {
        "variants": [
            {"seller_sku": "A", "price": 1000, "sale_props": {"Color": "Red"}},
            {"seller_sku": "B", "price": 1300, "sale_props": {"Color": "Blue"}},
        ]
    }
    report = apply_price_overrides(draft, price_override=999)
    assert report["single_override_refused"] is True
    assert draft["variants"][0]["price"] == 1000
    assert draft["variants"][1]["price"] == 1300


def test_per_variant_override_fills_unresolved():
    draft = {
        "variants": [
            {"seller_sku": "A", "price": 1000, "sale_props": {"Size": "S"}},
            {"seller_sku": "B", "price": None, "sale_props": {"Size": "L"}},
        ]
    }
    report = apply_price_overrides(
        draft, price_override=50, variant_price_overrides={"B": 1200}
    )
    assert report["per_variant_applied"] == 1
    assert draft["variants"][0]["price"] == 1000  # not flattened
    assert draft["variants"][1]["price"] == 1200
    assert report["single_override_refused"] is True


def test_validate_special_price_rules():
    draft = {
        "variants": [
            {"seller_sku": "A", "price": 1000, "special_price": 799, "sale_props": {}},
            {"seller_sku": "B", "price": 1000, "special_price": 1000, "sale_props": {}},
        ]
    }
    errs = validate_variant_pricing(draft)
    assert any("special_price_not_below_regular" in e for e in errs)


def test_create_xml_includes_special_price():
    draft = {
        "product": {
            "title": "X",
            "primary_category_id": 1,
            "brand": "No Brand",
            "attributes": {},
        },
        "media": {"resolved_images": ["https://static-01.daraz.pk/p/a.jpg"]},
        "variants": [
            {
                "seller_sku": "MTF-A",
                "price": 1000,
                "special_price": 799,
                "quantity": 1,
                "package_weight": 0.5,
                "package_length": 10,
                "package_width": 8,
                "package_height": 4,
                "sale_props": {"Color": "Red"},
            },
            {
                "seller_sku": "MTF-B",
                "price": 1200,
                "quantity": 1,
                "package_weight": 0.5,
                "package_length": 10,
                "package_width": 8,
                "package_height": 4,
                "sale_props": {"Color": "Blue"},
            },
        ],
    }
    xml = build_create_product_xml(draft)
    assert "<price>1000</price>" in xml
    assert "<special_price>799</special_price>" in xml
    assert "<price>1200</price>" in xml
    # Second SKU has no special — must not invent one
    assert xml.count("<special_price>") == 1


# --- public extraction -------------------------------------------------------


SKU_HTML = """
<script>
var __moduleData__ = {"data":{"root":{"fields":{
"productOption":{"skuBase":{"properties":[{"name":"Color Family","pid":"30129","values":[
{"name":"Red","vid":"1"},{"name":"Blue","vid":"2"}]}],
"skus":[
{"skuId":"111","propPath":"30129:1"},
{"skuId":"222","propPath":"30129:2"}
]}},
"skuInfos":{
"111":{"skuId":"111","categoryId":"10001948","price":{"price":1000,"specialPrice":799},"image":"https://static-01.daraz.pk/p/a.jpg"},
"222":{"skuId":"222","categoryId":"10001948","price":{"price":1200},"image":"https://static-01.daraz.pk/p/b.jpg"}
}
}}}};
</script>
"""


def test_extract_per_variant_regular_and_special():
    variants = _extract_variants_structured(SKU_HTML)
    assert len(variants) == 2
    by_id = {v["daraz_sku_id"]: v for v in variants}
    assert by_id["111"]["price"] == 1000
    assert by_id["111"]["special_price"] == 799
    assert by_id["222"]["price"] == 1200
    assert by_id["222"]["special_price"] is None
    assert by_id["111"]["sale_props"].get("Color Family") == "Red"


def test_never_flatten_fallback_across_variants():
    variants = [
        {"daraz_sku_id": "1", "price": None, "sale_props": {"C": "A"}},
        {"daraz_sku_id": "2", "price": None, "sale_props": {"C": "B"}},
    ]
    out = _apply_price_fallbacks(
        variants,
        pdt_price=8500,
        catalog={"original_price": 8500, "cheapest_sku_id": "1"},
        json_ld_price=None,
    )
    assert out[0]["price"] == 8500
    assert out[1]["price"] is None  # not flattened


def test_public_draft_preserves_different_variant_prices(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _store(tenancy_env, wid)
    _defaults(tenancy_env, wid)
    extracted = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i1.html",
        "item_id": "1",
        "title": "Bag Multi",
        "brand": "Bag Street",
        "description_html": "<p>Hi</p>",
        "images": ["https://static-01.daraz.pk/p/a.png"],
        "price": None,
        "category_hint": "Men Bags",
        "category_id": "10001948",
        "category_resolution": {
            "category_id": "10001948",
            "confidence": "high",
            "source": "catalog.categories",
        },
        "variants": [
            {
                "daraz_sku_id": "a",
                "price": 1000,
                "special_price": 799,
                "sale_props": {"Color": "Red", "Size": "S"},
                "price_confidence": "high",
            },
            {
                "daraz_sku_id": "b",
                "price": 1200,
                "special_price": 999,
                "sale_props": {"Color": "Red", "Size": "L"},
                "price_confidence": "high",
            },
            {
                "daraz_sku_id": "c",
                "price": 1100,
                "sale_props": {"Color": "Blue", "Size": "S"},
                "price_confidence": "high",
            },
        ],
        "provenance": {},
        "timings_ms": {},
        "warnings": [],
    }
    result = build_public_clone_draft_from_extracted(
        wid, extracted=extracted, destination_store_id="store-a"
    )
    prices = [v["price"] for v in result["draft"]["variants"]]
    specials = [v.get("special_price") for v in result["draft"]["variants"]]
    assert prices == [1000, 1200, 1100]
    assert specials == [799, 999, None]
    assert result["draft"]["product"]["primary_category_id"] == 10001948


def test_unresolved_variant_price_needs_attention(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _store(tenancy_env, wid)
    _defaults(tenancy_env, wid)
    extracted = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i1.html",
        "item_id": "1",
        "title": "Bag Multi",
        "brand": None,
        "description_html": "<p>Hi</p>",
        "images": ["https://static-01.daraz.pk/p/a.png"],
        "price": None,
        "category_id": "10001948",
        "category_resolution": {"confidence": "high", "source": "catalog"},
        "variants": [
            {"daraz_sku_id": "a", "price": 1000, "sale_props": {"C": "Red"}},
            {"daraz_sku_id": "b", "price": None, "sale_props": {"C": "Blue"}},
        ],
        "warnings": [],
        "timings_ms": {},
        "provenance": {},
    }
    result = build_public_clone_draft_from_extracted(
        wid, extracted=extracted, destination_store_id="store-a"
    )
    assert result["draft"]["unresolved_variants"]
    assert "missing_variant_prices" in result["draft"]["validation"]["errors"]


# --- brand / category --------------------------------------------------------


def test_unresolved_brand_falls_back_to_no_brand():
    from src.brand_resolve import clear_no_brand_cache_for_tests

    clear_no_brand_cache_for_tests()
    calls = []

    def query_brands(**kwargs):
        calls.append(kwargs.get("name"))
        return {"data": {"module": [{"name": "No Brand", "brand_id": 1}]}}

    res = resolve_brand_for_category(
        source_brand="Bag Street Unknown XYZ",
        primary_category_id=10001948,
        query_brands=query_brands,
    )
    assert res["status"] == "NO_BRAND"
    assert res["used_no_brand"] is True
    assert res["brand"] == "No Brand"
    assert "Bag Street" in (res.get("source_brand") or "")
    # Policy: never query for source brand name
    assert calls == ["No Brand"]


def test_missing_brand_uses_no_brand():
    def query_brands(**kwargs):
        return {"data": {"module": [{"name": "No Brand", "brand_id": 9}]}}

    res = resolve_brand_for_category(
        source_brand=None,
        primary_category_id=1,
        query_brands=query_brands,
    )
    assert res["status"] == "NO_BRAND"
    assert res["used_no_brand"] is True


# --- duplicates --------------------------------------------------------------


def test_classify_exact_vs_similar():
    kind, best = classify_duplicate_matches(
        [{"match_reason": "title_exact", "match_score": 1.0, "id": "1"}]
    )
    assert kind == "ALREADY_EXISTS"
    assert best["id"] == "1"
    kind2, _ = classify_duplicate_matches(
        [{"match_reason": "title_similar", "match_score": 0.7, "id": "2"}]
    )
    assert kind2 == "POSSIBLE_DUPLICATE"


def test_duplicate_local_index_after_create(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _store(tenancy_env, wid)
    draft = {
        "product": {
            "title": "Portable Mini USB Rechargeable Fan - 3 Speed",
            "primary_category_id": 10,
            "brand": "No Brand",
        },
        "media": {"resolved_images": ["https://static-01.daraz.pk/p/a.png"]},
        "variants": [
            {
                "seller_sku": "MTF-FAN",
                "source_daraz_sku_id": "99",
                "price": 500,
                "quantity": 1,
            }
        ],
    }
    up = _local_catalog_upsert(
        wid, store, item_id="555", draft=draft, live_item=None
    )
    assert up["ok"] is True
    found = tenancy_env.find_possible_product_duplicates(
        wid,
        str(store["id"]),
        title="Portable Mini USB Rechargeable Fan 3-Speed",
        category_id=10,
    )
    assert found
    assert found[0]["daraz_item_id"] == "555"


def test_multi_store_duplicate_does_not_block_other(tenancy_env):
    from src.auth import issue_test_token
    from fastapi.testclient import TestClient
    from src.app import app

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {issue_test_token('u-dup', email='d@x.com')}"}
    boot = client.post("/api/bootstrap", headers=headers)
    wid = boot.json()["workspace"]["id"]
    a = _store(tenancy_env, wid, "store-a")
    _store(tenancy_env, wid, "store-b")
    _defaults(tenancy_env, wid)

    # Seed duplicate only on store-a
    tenancy_env.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": str(a["id"]),
            "daraz_item_id": "999",
            "title": "Unique Glow Widget Alpha",
            "primary_category_id": 10001948,
        }
    )

    fake = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i123.html",
        "item_id": "123",
        "title": "Unique Glow Widget Alpha",
        "brand": "No Brand",
        "description_html": "<p>x</p>",
        "images": ["https://static-01.daraz.pk/p/a.jpg"],
        "price": 100,
        "category_id": "10001948",
        "category_resolution": {"confidence": "high", "source": "catalog"},
        "variants": [{"price": 100, "sale_props": {}, "daraz_sku_id": "1"}],
        "provenance": {},
        "timings_ms": {"fetch_extract": 1},
        "warnings": [],
    }

    with patch("src.product_add.fetch_public_product", return_value=fake):
        with patch("src.product_add.find_connected_owner_for_item", return_value=None):
            with patch("src.product_add.client_for_store") as client_fn:
                mock_client = MagicMock()
                mock_client.get_category_attributes.return_value = {"data": []}
                mock_client.query_category_brands.return_value = {
                    "data": {"module": [{"name": "No Brand", "brand_id": 1}]}
                }
                client_fn.return_value = mock_client
                with patch(
                    "src.image_migrate.DarazImageMigrationService.resolve_many"
                ) as resolve_many:
                    from src.image_migrate import ImageMigrationResult

                    resolve_many.return_value = [
                        ImageMigrationResult(
                            source_url="https://static-01.daraz.pk/p/a.jpg",
                            status="completed",
                            migrated_url="https://static-01.daraz.pk/p/a.jpg",
                            strategy="reuse_cdn",
                        )
                    ]
                    result = add_product_from_public_url(
                        wid,
                        "https://www.daraz.pk/products/x-i123.html",
                        ["store-a", "store-b"],
                        execute=False,
                    )

    statuses = {d["store"]["store_id"]: d["status"] for d in result["destinations"]}
    assert statuses["store-a"] == "ALREADY_EXISTS"
    assert statuses["store-b"] in {"READY", "NEEDS_ATTENTION", "BLOCKED_CREATE"} or statuses[
        "store-b"
    ] == "READY"


def test_no_full_sync_on_duplicate_check(tenancy_env):
    """Duplicate path must not call get_products (full catalog sync)."""
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _store(tenancy_env, wid)
    _defaults(tenancy_env, wid)
    fake = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i123.html",
        "item_id": "123",
        "title": "Fresh Unique Title ZZZ",
        "brand": "No Brand",
        "description_html": "<p>x</p>",
        "images": ["https://static-01.daraz.pk/p/a.jpg"],
        "price": 100,
        "category_id": "10001948",
        "category_resolution": {"confidence": "high", "source": "catalog"},
        "variants": [{"price": 100, "sale_props": {}, "daraz_sku_id": "1"}],
        "provenance": {},
        "timings_ms": {"fetch_extract": 1},
        "warnings": [],
    }
    with patch("src.product_add.fetch_public_product", return_value=fake):
        with patch("src.product_add.find_connected_owner_for_item", return_value=None):
            with patch("src.product_add.client_for_store") as client_fn:
                mock_client = MagicMock()
                mock_client.get_products = MagicMock(
                    side_effect=AssertionError("full sync not allowed")
                )
                mock_client.get_category_attributes.return_value = {"data": []}
                mock_client.query_category_brands.return_value = {
                    "data": {"module": [{"name": "No Brand"}]}
                }
                client_fn.return_value = mock_client
                with patch(
                    "src.image_migrate.DarazImageMigrationService.resolve_many",
                    return_value=[],
                ):
                    add_product_from_public_url(
                        wid,
                        "https://www.daraz.pk/products/x-i123.html",
                        ["store-a"],
                        execute=False,
                    )
    mock_client.get_products.assert_not_called()
