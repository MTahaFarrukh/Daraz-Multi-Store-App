"""Phase 4D.5.2B — generic variant semantics + always No Brand."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.brand_resolve import (
    clear_no_brand_cache_for_tests,
    resolve_brand_for_category,
    resolve_destination_no_brand,
)
from src.category_attr_resolve import (
    clear_category_attr_cache_for_tests,
    resolve_required_category_attributes,
)
from src.category_validate import validate_draft_against_category
from src.db import reset_repo_for_tests
from src.product_create_payload import build_create_product_xml
from src.public_daraz import (
    _extract_variants_structured,
    clear_public_cache_for_tests,
)
from src.variant_semantics import (
    classify_property_semantic,
    dimensions_from_prop_path,
    match_option_value,
    resolve_all_variant_sale_props,
)


COLOR_OPTS = [
    {"name": "Black"},
    {"name": "Pink"},
    {"name": "Purple"},
    {"name": "Green"},
    {"name": "Gray"},
    {"name": "White"},
]

SIZE_OPTS = [{"name": "Small"}, {"name": "Medium"}, {"name": "Large"}]
PACK_OPTS = [{"name": "Pack of 1"}, {"name": "Pack of 2"}, {"name": "2 Pieces"}]
CAP_OPTS = [{"name": "500ml"}, {"name": "1L"}]


def _schema(*extra: dict[str, Any]) -> dict[str, Any]:
    base = [
        {
            "name": "short_description",
            "attribute_type": "normal",
            "is_mandatory": 1,
        },
        {
            "name": "short_description_en",
            "attribute_type": "normal",
            "is_mandatory": 1,
        },
        {"name": "brand", "attribute_type": "normal", "is_mandatory": 1},
        {"name": "name", "attribute_type": "normal", "is_mandatory": 1},
        {"name": "SellerSku", "attribute_type": "sku", "is_mandatory": 1},
        {"name": "price", "attribute_type": "sku", "is_mandatory": 1},
    ]
    return {"data": base + list(extra)}


def _draft(
    variants: list[dict[str, Any]],
    *,
    title: str = "Test Product",
) -> dict[str, Any]:
    return {
        "product": {
            "title": title,
            "title_en": title,
            "brand": "No Brand",
            "primary_category_id": 10001948,
            "attributes": {"brand": "No Brand"},
            "description_html": f"<p>{title}</p>",
            "short_description_html": None,
        },
        "media": {"product_images": ["https://static-01.daraz.pk/p/a.jpg"]},
        "variants": variants,
        "validation": {},
    }


def _sku(
    sku_id: str,
    sale: dict[str, Any],
    *,
    price: float,
    dims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from src.variant_semantics import dimensions_from_sale_props

    return {
        "seller_sku": f"MTF-{sku_id}",
        "daraz_sku_id": sku_id,
        "price": price,
        "quantity": 1,
        "package_weight": 0.5,
        "package_length": 10,
        "package_width": 8,
        "package_height": 4,
        "sale_props": dict(sale),
        "dimensions": dims
        if dims is not None
        else dimensions_from_sale_props(sale),
    }


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
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_public_cache_for_tests()
    clear_category_attr_cache_for_tests()
    clear_no_brand_cache_for_tests()
    return reset_repo_for_tests()


def test_case_a_color_maps_to_color_family():
    schema = _schema(
        {
            "name": "color_family",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": COLOR_OPTS,
        }
    )
    draft = _draft(
        [
            _sku(str(i), {"Color": c}, price=1000 + i)
            for i, c in enumerate(["Black", "Pink", "Purple", "Green"], start=101)
        ]
    )
    report = resolve_required_category_attributes(draft, schema)
    assert report["compatibility"]["compatible"] is True
    colors = [v["sale_props"]["color_family"] for v in draft["variants"]]
    assert colors == ["Black", "Pink", "Purple", "Green"]
    assert all("Color" not in v["sale_props"] for v in draft["variants"])
    assert validate_draft_against_category(draft, schema)["valid"] is True


def test_case_b_pack_not_color_family():
    schema = _schema(
        {
            "name": "color_family",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": COLOR_OPTS,
        }
    )
    draft = _draft(
        [
            _sku("201", {"Pack": "Pack of 1"}, price=500),
            _sku("202", {"Pack": "Pack of 2"}, price=900),
        ]
    )
    report = resolve_required_category_attributes(draft, schema)
    for v in draft["variants"]:
        assert "color_family" not in (v.get("sale_props") or {})
        assert (v.get("sale_props") or {}).get("color_family") not in {
            "Pack of 1",
            "Pack of 2",
        }
    assert report["compatibility"]["compatible"] is False
    msgs = [u.get("message") or "" for u in report["unresolved"]]
    assert any("Pack" in m and "color" in m.lower() for m in msgs)
    result = validate_draft_against_category(draft, schema)
    assert result["valid"] is False
    assert "sku:color_family" in result["missing_required"]


def test_case_b_pack_maps_to_pack_size_when_schema_supports():
    schema = _schema(
        {
            "name": "pack_size",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": PACK_OPTS,
        }
    )
    draft = _draft(
        [
            _sku("201", {"Pack": "Pack of 1"}, price=500),
            _sku("202", {"Pack": "Pack of 2"}, price=900),
        ]
    )
    resolve_required_category_attributes(draft, schema)
    assert draft["variants"][0]["sale_props"]["pack_size"] == "Pack of 1"
    assert draft["variants"][1]["sale_props"]["pack_size"] == "Pack of 2"
    assert validate_draft_against_category(draft, schema)["valid"] is True


def test_case_c_size_maps_to_size():
    schema = _schema(
        {
            "name": "size",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": SIZE_OPTS,
        }
    )
    draft = _draft(
        [
            _sku(str(i), {"Size": s}, price=200 + i)
            for i, s in enumerate(["Small", "Medium", "Large"], start=1)
        ]
    )
    resolve_required_category_attributes(draft, schema)
    assert [v["sale_props"]["size"] for v in draft["variants"]] == [
        "Small",
        "Medium",
        "Large",
    ]


def test_case_d_capacity():
    schema = _schema(
        {
            "name": "capacity",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": CAP_OPTS,
        }
    )
    draft = _draft(
        [
            _sku("1", {"Capacity": "500ml"}, price=100),
            _sku("2", {"Capacity": "1L"}, price=150),
        ]
    )
    resolve_required_category_attributes(draft, schema)
    assert draft["variants"][0]["sale_props"]["capacity"] == "500ml"
    assert draft["variants"][1]["sale_props"]["capacity"] == "1L"


def test_case_e_color_x_size():
    schema = _schema(
        {
            "name": "color_family",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": COLOR_OPTS,
        },
        {
            "name": "size",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": SIZE_OPTS,
        },
    )
    combos = [
        ("Black", "Small"),
        ("Black", "Large"),
        ("Pink", "Small"),
        ("Pink", "Large"),
    ]
    draft = _draft(
        [
            _sku(str(i), {"Color": c, "Size": s}, price=500 + i)
            for i, (c, s) in enumerate(combos, start=1)
        ]
    )
    resolve_required_category_attributes(draft, schema)
    for v, (c, s) in zip(draft["variants"], combos, strict=True):
        assert v["sale_props"]["color_family"] == c
        assert v["sale_props"]["size"] == s
    xml = build_create_product_xml(draft)
    assert xml.count("<color_family>") == 4
    assert xml.count("<size>") == 4
    assert validate_draft_against_category(draft, schema)["valid"] is True


def test_case_f_color_x_pack():
    schema = _schema(
        {
            "name": "color_family",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": COLOR_OPTS,
        },
        {
            "name": "pack_size",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": PACK_OPTS,
        },
    )
    combos = [
        ("Black", "Pack of 1"),
        ("Black", "Pack of 2"),
        ("Pink", "Pack of 1"),
        ("Pink", "Pack of 2"),
    ]
    draft = _draft(
        [
            _sku(str(i), {"Color": c, "Pack": p}, price=400 + i * 50)
            for i, (c, p) in enumerate(combos, start=1)
        ]
    )
    resolve_required_category_attributes(draft, schema)
    for v, (c, p) in zip(draft["variants"], combos, strict=True):
        assert v["sale_props"]["color_family"] == c
        assert v["sale_props"]["pack_size"] == p


def test_case_g_incompatible_pack_vs_required_color():
    schema = _schema(
        {
            "name": "color_family",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": COLOR_OPTS,
        }
    )
    draft = _draft(
        [
            _sku("201", {"Pack": "Pack of 1"}, price=500),
            _sku("202", {"Pack": "Pack of 2"}, price=900),
        ]
    )
    report = resolve_all_variant_sale_props(draft, schema["data"])
    compat = report["compatibility"]
    assert compat["compatible"] is False
    assert compat["source_semantics"] == ["pack"]
    assert any(u["attribute"] == "color_family" for u in compat["required_unsatisfied"])
    for v in draft["variants"]:
        assert v["sale_props"].get("color_family") is None


def test_case_h_prices_remain_per_sku():
    schema = _schema(
        {
            "name": "pack_size",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": PACK_OPTS,
        }
    )
    draft = _draft(
        [
            _sku("201", {"Pack": "Pack of 1"}, price=500),
            _sku("202", {"Pack": "Pack of 2"}, price=900),
        ]
    )
    resolve_required_category_attributes(draft, schema)
    assert draft["variants"][0]["price"] == 500
    assert draft["variants"][1]["price"] == 900
    assert draft["variants"][0]["daraz_sku_id"] == "201"


def test_extract_preserves_pack_property_name():
    html = """
    <script>
    var __moduleData__ = {"data":{"root":{"fields":{
      "productOption":{"skuBase":{"properties":[{"name":"Pack","pid":"9","values":[
        {"name":"Pack of 1","vid":"a"},{"name":"Pack of 2","vid":"b"}]}],
        "skus":[
          {"skuId":"201","propPath":"9:a"},
          {"skuId":"202","propPath":"9:b"}
        ]}},
      "skuInfos":{
        "201":{"skuId":"201","categoryId":"1","price":{"originalPrice":{"value":500}}},
        "202":{"skuId":"202","categoryId":"1","price":{"originalPrice":{"value":900}}}
      }
    }}}};
    </script>
    """
    variants = _extract_variants_structured(html)
    assert len(variants) == 2
    assert variants[0]["sale_props"]["Pack"] == "Pack of 1"
    assert variants[0]["dimensions"][0]["source_property_name"] == "Pack"
    assert variants[0]["dimensions"][0]["semantic_type"] == "pack"
    assert variants[0]["dimensions"][0]["source_property_id"] == "9"
    assert classify_property_semantic("Pack")["semantic_type"] == "pack"


def test_extract_preserves_color_property():
    html = """
    <script>
    var __moduleData__ = {"data":{"root":{"fields":{
      "productOption":{"skuBase":{"properties":[{"name":"Color","pid":"1","values":[
        {"name":"Black","vid":"a"},{"name":"Pink","vid":"b"}]}],
        "skus":[
          {"skuId":"101","propPath":"1:a"},
          {"skuId":"102","propPath":"1:b"}
        ]}},
      "skuInfos":{
        "101":{"skuId":"101"},
        "102":{"skuId":"102"}
      }
    }}}};
    </script>
    """
    variants = _extract_variants_structured(html)
    assert variants[0]["dimensions"][0]["semantic_type"] == "color"
    assert variants[0]["dimensions"][0]["source_value"] == "Black"


def test_grey_gray_still_safe():
    assert match_option_value("grey", ["Gray", "Black"]) == "Gray"
    assert match_option_value("navy", ["Black", "Blue"]) is None


def test_brand_source_nike_becomes_no_brand():
    clear_no_brand_cache_for_tests()
    calls: list[str] = []

    def query(**kwargs):
        calls.append(str(kwargs.get("name")))
        return {"data": {"module": [{"name": "No Brand", "brand_id": 1}]}}

    res = resolve_brand_for_category(
        source_brand="Nike",
        primary_category_id=100,
        query_brands=query,
    )
    assert res["status"] == "NO_BRAND"
    assert res["brand"] == "No Brand"
    assert res["source_brand"] == "Nike"
    assert res["source_brand_ignored"] is True
    assert calls == ["No Brand"]


def test_brand_missing_still_no_brand():
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {"data": {"module": [{"name": "No Brand", "brand_id": 2}]}}

    res = resolve_brand_for_category(
        source_brand=None,
        primary_category_id=100,
        query_brands=query,
    )
    assert res["status"] == "NO_BRAND"
    assert res["brand"] == "No Brand"


def test_brand_recognized_still_no_brand():
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {
            "data": {
                "module": [
                    {"name": "No Brand", "brand_id": 1},
                    {"name": "Acme", "brand_id": 99},
                ]
            }
        }

    res = resolve_brand_for_category(
        source_brand="Acme",
        primary_category_id=100,
        query_brands=query,
        marketplace="pk",
    )
    assert res["status"] == "NO_BRAND"
    assert res["brand"] == "No Brand"
    assert res["source_brand"] == "Acme"


def test_no_brand_cached_by_marketplace_category():
    clear_no_brand_cache_for_tests()
    calls = {"n": 0}

    def query(**kwargs):
        calls["n"] += 1
        return {"data": {"module": [{"name": "No Brand", "brand_id": 1}]}}

    a = resolve_destination_no_brand(
        primary_category_id=55, query_brands=query, marketplace="pk"
    )
    b = resolve_destination_no_brand(
        primary_category_id=55, query_brands=query, marketplace="pk"
    )
    assert a["brand"] == "No Brand"
    assert b.get("cache_hit") is True
    assert calls["n"] == 1


def test_no_brand_unavailable_needs_attention():
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {
            "data": {
                "module": [
                    {"name": "Nike", "brand_id": 10},
                    {"name": "Adidas", "brand_id": 11},
                ]
            }
        }

    res = resolve_brand_for_category(
        source_brand="Nike",
        primary_category_id=100,
        query_brands=query,
    )
    assert res["status"] == "UNRESOLVED"
    assert res["reason"] == "no_brand_unavailable"
    assert res["brand"] is None


def test_multi_store_each_destination_no_brand(tenancy_env):
    from fastapi.testclient import TestClient

    from src.app import app
    from src.auth import issue_test_token
    from src.image_migrate import ImageMigrationResult
    from src.product_add import add_product_from_public_url

    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {issue_test_token('u-52b', email='b@x.com')}"
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

    schema = _schema(
        {
            "name": "color_family",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": COLOR_OPTS,
        }
    )
    fake = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/x-i1.html",
        "item_id": "1",
        "title": "Color Bag",
        "brand": "Nike",
        "description_html": "<p>Color Bag</p>",
        "images": ["https://static-01.daraz.pk/p/a.jpg"],
        "category_id": "10001948",
        "category_resolution": {"confidence": "high", "source": "catalog"},
        "variants": [
            {
                "daraz_sku_id": str(i),
                "price": 1000 + i,
                "sale_props": {"Color": c},
                "dimensions": [
                    {
                        "source_property_name": "Color",
                        "source_value": c,
                        "semantic_type": "color",
                        "confidence": "high",
                    }
                ],
                "price_source": "mtop.getDetailInfo",
            }
            for i, c in enumerate(["Black", "Pink"], start=1)
        ],
        "pricing_summary": {
            "regular_prices_complete": True,
            "missing_variant_prices": 0,
        },
        "timings_ms": {},
        "warnings": [],
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
                    result = add_product_from_public_url(
                        wid,
                        "https://www.daraz.pk/products/x-i1.html",
                        ["store-a", "store-b"],
                        execute=False,
                    )

    assert result["needs_attention_count"] == 0
    for d in result["destinations"]:
        assert d.get("used_no_brand") is True
        brand_res = d.get("brand_resolution") or {}
        assert brand_res.get("brand") == "No Brand"
        assert brand_res.get("source_brand") == "Nike"
        preview = d.get("draft_preview") or {}
        assert (preview.get("Attributes") or {}).get("brand") == "No Brand"


def test_dimensions_from_prop_path_multi():
    props = [
        {
            "pid": "1",
            "name": "Color",
            "values": [{"vid": "a", "name": "Black"}, {"vid": "b", "name": "Pink"}],
        },
        {
            "pid": "2",
            "name": "Size",
            "values": [{"vid": "s", "name": "Small"}, {"vid": "l", "name": "Large"}],
        },
    ]
    sale, dims = dimensions_from_prop_path("1:a;2:s", props)
    assert sale == {"Color": "Black", "Size": "Small"}
    assert len(dims) == 2
    assert dims[0]["semantic_type"] == "color"
    assert dims[1]["semantic_type"] == "size"
