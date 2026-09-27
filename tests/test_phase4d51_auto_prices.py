"""Phase 4D.5.1 — automatic public variant price resolution via getDetailInfo."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.product_add import add_product_from_public_url
from src.public_daraz import (
    build_public_clone_draft_from_extracted,
    clear_public_cache_for_tests,
    merge_detail_prices_into_variants,
    sku_prices_from_detail_fields,
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
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_public_cache_for_tests()
    return reset_repo_for_tests()


DETAIL_FIELDS = {
    "skuInfos": {
        "0": {
            "skuId": "101",
            "price": {
                "originalPrice": {"value": 1000, "text": "Rs. 1,000"},
                "salePrice": {"value": 799, "text": "Rs. 799"},
                "discount": "-20%",
            },
        },
        "101": {
            "skuId": "101",
            "categoryId": "10001948",
            "price": {
                "originalPrice": {"value": 1000, "text": "Rs. 1,000"},
                "salePrice": {"value": 799, "text": "Rs. 799"},
            },
        },
        "102": {
            "skuId": "102",
            "categoryId": "10001948",
            "price": {
                "originalPrice": {"value": 1200, "text": "Rs. 1,200"},
                "salePrice": {"value": 900, "text": "Rs. 900"},
            },
        },
        "103": {
            "skuId": "103",
            "categoryId": "10001948",
            "price": {
                "originalPrice": {"value": 1100, "text": "Rs. 1,100"},
                "salePrice": {"value": 850, "text": "Rs. 850"},
            },
        },
        "104": {
            "skuId": "104",
            "categoryId": "10001948",
            "price": {
                "originalPrice": {"value": 1300, "text": "Rs. 1,300"},
                "salePrice": {"value": 999, "text": "Rs. 999"},
            },
        },
    }
}


def test_detail_fields_extract_per_sku_original_as_regular():
    prices = sku_prices_from_detail_fields(DETAIL_FIELDS)
    assert set(prices) == {"101", "102", "103", "104"}
    assert prices["101"]["regular_price"] == 1000
    assert prices["102"]["regular_price"] == 1200
    assert prices["103"]["regular_price"] == 1100
    assert prices["104"]["regular_price"] == 1300
    assert prices["101"]["current_price"] == 799
    assert prices["101"]["special_price"] is None


def test_live_failure_class_ssr_missing_detail_resolves_all():
    """Exact LIVE failure: SSR identities without prices → detail fills all."""
    variants = [
        {"daraz_sku_id": "101", "price": None, "sale_props": {"Color": "Black"}},
        {"daraz_sku_id": "102", "price": None, "sale_props": {"Color": "Pink"}},
        {"daraz_sku_id": "103", "price": None, "sale_props": {"Color": "Purple"}},
        {"daraz_sku_id": "104", "price": None, "sale_props": {"Color": "Green"}},
    ]
    prices = sku_prices_from_detail_fields(DETAIL_FIELDS)
    merged = merge_detail_prices_into_variants(variants, prices)
    assert [v["price"] for v in merged] == [1000, 1200, 1100, 1300]
    assert all(v.get("special_price") is None for v in merged)
    assert all(
        str(v.get("price_source")).startswith("mtop.getDetailInfo") for v in merged
    )


def test_merge_by_sku_id_not_array_order():
    variants = [
        {"daraz_sku_id": "104", "price": None, "sale_props": {"Color": "Green"}},
        {"daraz_sku_id": "101", "price": None, "sale_props": {"Color": "Black"}},
    ]
    prices = sku_prices_from_detail_fields(DETAIL_FIELDS)
    merged = merge_detail_prices_into_variants(variants, prices)
    assert merged[0]["price"] == 1300
    assert merged[1]["price"] == 1000


def test_color_x_size_combinations():
    fields = {
        "skuInfos": {
            "1": {"skuId": "1", "price": {"originalPrice": {"value": 900}}},
            "2": {"skuId": "2", "price": {"originalPrice": {"value": 1000}}},
            "3": {"skuId": "3", "price": {"originalPrice": {"value": 950}}},
            "4": {"skuId": "4", "price": {"originalPrice": {"value": 1050}}},
        }
    }
    variants = [
        {
            "daraz_sku_id": "1",
            "price": None,
            "sale_props": {"Color": "Black", "Size": "Small"},
        },
        {
            "daraz_sku_id": "2",
            "price": None,
            "sale_props": {"Color": "Black", "Size": "Large"},
        },
        {
            "daraz_sku_id": "3",
            "price": None,
            "sale_props": {"Color": "Pink", "Size": "Small"},
        },
        {
            "daraz_sku_id": "4",
            "price": None,
            "sale_props": {"Color": "Pink", "Size": "Large"},
        },
    ]
    merged = merge_detail_prices_into_variants(
        variants, sku_prices_from_detail_fields(fields)
    )
    assert [v["price"] for v in merged] == [900, 1000, 950, 1050]


def test_draft_no_missing_price_after_detail(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    tenancy_env.upsert_store(
        wid,
        {
            "store_id": "store-a",
            "account": "a@x.com",
            "seller_id": "s",
            "display_name": "A",
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
    extracted = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i1.html",
        "item_id": "1",
        "title": "Color Bag",
        "brand": "No Brand",
        "description_html": "<p>x</p>",
        "images": ["https://static-01.daraz.pk/p/a.png"],
        "price": None,
        "category_id": "10001948",
        "category_resolution": {"confidence": "high", "source": "catalog"},
        "variants": [
            {
                "daraz_sku_id": "101",
                "price": 1000,
                "sale_props": {"Color": "Black"},
                "price_source": "mtop.getDetailInfo",
                "price_confidence": "high",
            },
            {
                "daraz_sku_id": "102",
                "price": 1200,
                "sale_props": {"Color": "Pink"},
                "price_source": "mtop.getDetailInfo",
                "price_confidence": "high",
            },
            {
                "daraz_sku_id": "103",
                "price": 1100,
                "sale_props": {"Color": "Purple"},
                "price_source": "mtop.getDetailInfo",
                "price_confidence": "high",
            },
            {
                "daraz_sku_id": "104",
                "price": 1300,
                "sale_props": {"Color": "Green"},
                "price_source": "mtop.getDetailInfo",
                "price_confidence": "high",
            },
        ],
        "pricing_summary": {
            "regular_prices_complete": True,
            "missing_variant_prices": 0,
        },
        "warnings": [],
        "timings_ms": {},
        "provenance": {},
    }
    result = build_public_clone_draft_from_extracted(
        wid, extracted=extracted, destination_store_id="store-a"
    )
    assert not result["draft"].get("unresolved_variants")
    assert [v["price"] for v in result["draft"]["variants"]] == [1000, 1200, 1100, 1300]


def test_add_product_does_not_need_attention_when_prices_resolved(tenancy_env):
    from fastapi.testclient import TestClient

    from src.app import app
    from src.auth import issue_test_token
    from src.image_migrate import ImageMigrationResult

    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {issue_test_token('u-451', email='p@x.com')}"
    }
    boot = client.post("/api/bootstrap", headers=headers)
    wid = boot.json()["workspace"]["id"]
    for sid in ("store-a", "store-b"):
        tenancy_env.upsert_store(
            wid,
            {
                "store_id": sid,
                "account": f"{sid}@x.com",
                "seller_id": f"s-{sid}",
                "display_name": sid,
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
        "source_url": "https://www.daraz.pk/products/x-i123.html",
        "item_id": "123",
        "title": "Auto Price Bag",
        "brand": "No Brand",
        "description_html": "<p>x</p>",
        "images": ["https://static-01.daraz.pk/p/a.jpg"],
        "price": None,
        "category_id": "10001948",
        "category_resolution": {"confidence": "high", "source": "catalog"},
        "variants": [
            {
                "daraz_sku_id": str(i),
                "price": 1000 + i * 100,
                "sale_props": {"Color": c},
                "price_source": "mtop.getDetailInfo",
            }
            for i, c in enumerate(["Black", "Pink", "Purple", "Green"], start=1)
        ],
        "pricing_summary": {
            "regular_prices_complete": True,
            "missing_variant_prices": 0,
            "price_resolution_source": "mtop.getDetailInfo",
        },
        "provenance": {"price": "mtop.getDetailInfo"},
        "timings_ms": {"fetch_extract": 50, "price_resolution_ms": 120},
        "warnings": [],
    }

    with patch("src.product_add.fetch_public_product", return_value=fake) as fetch_mock:
        with patch("src.product_add.find_connected_owner_for_item", return_value=None):
            with patch("src.product_add.client_for_store") as client_fn:
                mock_client = MagicMock()
                mock_client.get_category_attributes.return_value = {"data": []}
                mock_client.query_category_brands.return_value = {
                    "data": {"module": [{"name": "No Brand", "brand_id": 1}]}
                }
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
                    with patch(
                        "src.product_add.validate_draft_against_category",
                        return_value={
                            "valid": True,
                            "missing_required": [],
                            "invalid_values": [],
                        },
                    ):
                        result = add_product_from_public_url(
                            wid,
                            "https://www.daraz.pk/products/x-i123.html",
                            ["store-a", "store-b"],
                            execute=False,
                        )

    assert fetch_mock.call_count == 1
    assert result["needs_attention_count"] == 0
    for d in result["destinations"]:
        assert d.get("reason") != "missing_price"
        assert d["status"] != "NEEDS_ATTENTION"


def test_fetch_calls_detail_when_ssr_prices_missing(monkeypatch):
    clear_public_cache_for_tests()
    html = """
    <script type="application/ld+json">
    {"@type":"Product","name":"Bag","image":["https://static-01.daraz.pk/p/a.jpg"],
     "brand":{"@type":"Brand","name":"No Brand"},"description":"d"}
    </script>
    <script>
    var __moduleData__ = {"data":{"root":{"fields":{
      "productOption":{"skuBase":{"properties":[{"name":"Color","pid":"1","values":[
        {"name":"Black","vid":"a"},{"name":"Pink","vid":"b"},
        {"name":"Purple","vid":"c"},{"name":"Green","vid":"d"}]}],
        "skus":[
          {"skuId":"101","propPath":"1:a"},
          {"skuId":"102","propPath":"1:b"},
          {"skuId":"103","propPath":"1:c"},
          {"skuId":"104","propPath":"1:d"}
        ]}},
      "skuInfos":{
        "101":{"skuId":"101","categoryId":"10001948"},
        "102":{"skuId":"102","categoryId":"10001948"},
        "103":{"skuId":"103","categoryId":"10001948"},
        "104":{"skuId":"104","categoryId":"10001948"}
      }
    }}}};
    </script>
    """

    class FakeResp:
        status_code = 200
        content = html.encode("utf-8")
        encoding = "utf-8"
        headers: dict[str, str] = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **k):
            return FakeResp()

    detail_prices = sku_prices_from_detail_fields(DETAIL_FIELDS)

    monkeypatch.setattr("src.public_daraz.httpx.Client", FakeClient)
    monkeypatch.setattr("src.public_daraz._resolve_public", lambda host: None)
    monkeypatch.setattr(
        "src.public_daraz._fetch_catalog_enrichment",
        lambda *a, **k: {"timings_ms": {"catalog": 1}},
    )
    monkeypatch.setattr(
        "src.public_daraz._fetch_pdp_detail_sku_prices",
        lambda *a, **k: {
            "ok": True,
            "prices_by_sku": detail_prices,
            "source": "mtop.global.detail.web.getDetailInfo",
            "sku_count": 4,
            "timings_ms": {"price_resolution_ms": 88, "fallback_fetch_ms": 70},
        },
    )

    from src.public_daraz import fetch_public_product

    payload = fetch_public_product(
        "https://www.daraz.pk/products/foo-i433265727.html", use_cache=False
    )
    assert payload["pricing_summary"]["missing_variant_prices"] == 0
    assert payload["pricing_summary"]["regular_prices_complete"] is True
    assert [v["price"] for v in payload["variants"]] == [1000, 1200, 1100, 1300]
    assert payload["provenance"]["price"] == "mtop.getDetailInfo"
    assert payload["timings_ms"].get("price_resolution_ms") == 88
