"""Phase 4D.5.2 — auto-resolve required Daraz category attributes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.category_attr_resolve import (
    build_short_description,
    clear_category_attr_cache_for_tests,
    get_cached_category_attributes,
    match_option_value,
    resolve_required_category_attributes,
)
from src.category_validate import validate_draft_against_category
from src.db import reset_repo_for_tests
from src.product_create_payload import build_create_product_xml
from src.public_daraz import clear_public_cache_for_tests


LIVE_ATTR_SCHEMA: dict[str, Any] = {
    "data": [
        {
            "name": "short_description",
            "attribute_type": "normal",
            "is_mandatory": 1,
            "max_length": 500,
        },
        {
            "name": "short_description_en",
            "attribute_type": "normal",
            "is_mandatory": 1,
            "max_length": 500,
        },
        {
            "name": "color_family",
            "attribute_type": "sku",
            "is_mandatory": 1,
            "options": [
                {"name": "Black"},
                {"name": "Pink"},
                {"name": "Purple"},
                {"name": "Green"},
                {"name": "Gray"},
                {"name": "White"},
            ],
        },
        {
            "name": "SellerSku",
            "attribute_type": "sku",
            "is_mandatory": 1,
        },
        {
            "name": "price",
            "attribute_type": "sku",
            "is_mandatory": 1,
        },
        {
            "name": "brand",
            "attribute_type": "normal",
            "is_mandatory": 1,
        },
        {
            "name": "name",
            "attribute_type": "normal",
            "is_mandatory": 1,
        },
    ]
}


def _bag_draft(*, short: str | None = None, desc: str | None = None) -> dict[str, Any]:
    product: dict[str, Any] = {
        "title": "Portable lightweight shoulder bag with adjustable strap",
        "title_en": "Portable lightweight shoulder bag with adjustable strap",
        "brand": "No Brand",
        "primary_category_id": 10001948,
        "attributes": {"brand": "No Brand"},
        "description_html": desc
        or (
            "<p>Portable lightweight shoulder bag with adjustable strap. "
            "Perfect for everyday use.</p>"
            "<script>evil()</script>"
        ),
        "short_description_html": short,
    }
    colors = ["Black", "Pink", "Purple", "Green"]
    variants = []
    for i, color in enumerate(colors, start=101):
        variants.append(
            {
                "seller_sku": f"MTF-{i}",
                "daraz_sku_id": str(i),
                "price": 1000 + i,
                "quantity": 1,
                "package_weight": 0.5,
                "package_length": 10,
                "package_width": 8,
                "package_height": 4,
                "sale_props": {"Color": color},
            }
        )
    return {
        "product": product,
        "media": {"product_images": ["https://static-01.daraz.pk/p/a.jpg"]},
        "variants": variants,
        "validation": {},
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
    return reset_repo_for_tests()


# --- short description -------------------------------------------------------


def test_short_description_from_source_short():
    draft = _bag_draft(short="<p>Compact everyday shoulder bag.</p>")
    built = build_short_description(draft)
    assert built["ok"] is True
    assert built["source"] == "source_short_html"
    assert "Compact everyday shoulder bag" in built["value"]


def test_short_description_from_sanitized_description():
    draft = _bag_draft(short=None)
    draft["product"]["description_html"] = (
        "<p>Soft canvas tote with zipper pocket.</p><style>.x{}</style>"
    )
    built = build_short_description(draft)
    assert built["ok"] is True
    assert built["source"] == "sanitized_description"
    assert "Soft canvas tote" in built["value"]
    assert "<script" not in (built["value"] or "").lower()
    assert "<style" not in (built["value"] or "").lower()


def test_short_description_title_fallback():
    draft = _bag_draft(short=None)
    draft["product"]["description_html"] = ""
    draft["product"]["attributes"] = {"brand": "No Brand"}
    built = build_short_description(draft)
    assert built["ok"] is True
    assert built["source"] == "title_fallback"
    assert "shoulder bag" in built["value"].lower()


def test_short_description_en_reuses_english():
    draft = _bag_draft(short=None)
    report = resolve_required_category_attributes(draft, LIVE_ATTR_SCHEMA)
    attrs = draft["product"]["attributes"]
    assert attrs.get("short_description")
    assert attrs.get("short_description_en")
    assert attrs["short_description"] == attrs["short_description_en"]
    assert draft["product"]["short_description_html"]
    assert report["unresolved_count"] == 0


# --- color_family ------------------------------------------------------------


def test_sku_color_family_exact_mapping():
    draft = _bag_draft()
    report = resolve_required_category_attributes(draft, LIVE_ATTR_SCHEMA)
    colors = [v["sale_props"]["color_family"] for v in draft["variants"]]
    assert colors == ["Black", "Pink", "Purple", "Green"]
    assert all("Color" not in v["sale_props"] for v in draft["variants"])
    assert report["unresolved_count"] == 0


def test_multiple_sku_colors_in_create_xml():
    draft = _bag_draft()
    resolve_required_category_attributes(draft, LIVE_ATTR_SCHEMA)
    xml = build_create_product_xml(draft)
    for color in ("Black", "Pink", "Purple", "Green"):
        assert f"<color_family>{color}</color_family>" in xml
    assert xml.count("<saleProp>") == 4


def test_gray_grey_safe_normalization():
    assert match_option_value("grey", ["Gray", "Black"]) == "Gray"
    assert match_option_value("gray", ["Grey", "Black"]) == "Grey"
    # Aggressive mappings must NOT happen
    assert match_option_value("navy", ["Black", "Blue"]) is None
    assert match_option_value("cream", ["White", "Beige"]) is None


def test_ambiguous_color_remains_unresolved():
    draft = _bag_draft()
    draft["variants"][0]["sale_props"] = {"Color": "Midnight Nebula"}
    report = resolve_required_category_attributes(draft, LIVE_ATTR_SCHEMA)
    assert any(
        u.get("attribute") == "color_family" and u.get("variant_index") == 0
        for u in report["unresolved"]
    )
    result = validate_draft_against_category(draft, LIVE_ATTR_SCHEMA)
    assert "sku:color_family" in result["missing_required"] or any(
        i.get("attribute") == "color_family" for i in result["invalid_values"]
    )


def test_light_purple_maps_to_purple_when_unique():
    assert match_option_value("Light Purple", ["Purple", "Black"]) == "Purple"


# --- generic resolver + live failure -----------------------------------------


def test_live_failure_fixture_fully_resolves():
    """Regression: short_description/_en + per-SKU color_family → CreateProduct eligible."""
    draft = _bag_draft(short=None)
    report = resolve_required_category_attributes(draft, LIVE_ATTR_SCHEMA)
    assert report["unresolved_count"] == 0
    result = validate_draft_against_category(draft, LIVE_ATTR_SCHEMA)
    assert result["valid"] is True
    assert result["missing_required"] == []
    assert draft["product"]["attributes"]["short_description"]
    assert draft["product"]["attributes"]["short_description_en"]
    for v, color in zip(
        draft["variants"], ["Black", "Pink", "Purple", "Green"], strict=True
    ):
        assert v["sale_props"]["color_family"] == color


def test_unknown_mandatory_material_needs_attention():
    schema = {
        "data": LIVE_ATTR_SCHEMA["data"]
        + [
            {
                "name": "material",
                "attribute_type": "normal",
                "is_mandatory": 1,
                "options": [{"name": "Plastic"}, {"name": "Cotton"}],
            }
        ]
    }
    draft = _bag_draft()
    report = resolve_required_category_attributes(draft, schema)
    assert any(u.get("attribute") == "material" for u in report["unresolved"])
    result = validate_draft_against_category(draft, schema)
    assert any("material" in m for m in result["missing_required"])


def test_product_and_sku_level_resolution():
    draft = _bag_draft(short=None)
    report = resolve_required_category_attributes(draft, LIVE_ATTR_SCHEMA)
    normal = [r for r in report["resolved"] if r.get("scope") == "normal"]
    sku = [r for r in report["resolved"] if r.get("scope") == "sku"]
    assert any(r["attribute"].startswith("short_description") for r in normal)
    assert len([r for r in sku if r["attribute"] == "color_family"]) == 4


def test_category_metadata_caching():
    clear_category_attr_cache_for_tests()
    calls: list[Any] = []

    def fetcher(cid):
        calls.append(cid)
        return {"data": [{"name": "brand", "is_mandatory": 0}]}

    a = get_cached_category_attributes(10001948, fetcher, marketplace="pk")
    b = get_cached_category_attributes(10001948, fetcher, marketplace="pk")
    assert a == b or a.get("data") == b.get("data")
    assert len(calls) == 1
    # Different marketplace → separate cache entry
    get_cached_category_attributes(10001948, fetcher, marketplace="bd")
    assert len(calls) == 2


def test_multi_store_source_once_dest_schema_twice(tenancy_env):
    """Source attrs derived once; each destination validates against its schema."""
    from src.product_add import add_product_from_public_url
    from src.image_migrate import ImageMigrationResult
    from fastapi.testclient import TestClient
    from src.app import app
    from src.auth import issue_test_token

    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {issue_test_token('u-452', email='a@x.com')}"
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
        "source_url": "https://www.daraz.pk/products/bag-i433265727.html",
        "item_id": "433265727",
        "title": "Portable lightweight shoulder bag with adjustable strap",
        "brand": "No Brand",
        "description_html": "<p>Portable lightweight shoulder bag with adjustable strap.</p>",
        "images": ["https://static-01.daraz.pk/p/a.jpg"],
        "price": None,
        "category_id": "10001948",
        "category_resolution": {"confidence": "high", "source": "catalog"},
        "variants": [
            {
                "daraz_sku_id": str(i),
                "price": 1000 + i * 50,
                "sale_props": {"Color": c},
                "price_source": "mtop.getDetailInfo",
            }
            for i, c in enumerate(["Black", "Pink", "Purple", "Green"], start=101)
        ],
        "pricing_summary": {
            "regular_prices_complete": True,
            "missing_variant_prices": 0,
            "price_resolution_source": "mtop.getDetailInfo",
        },
        "provenance": {"price": "mtop.getDetailInfo"},
        "timings_ms": {"fetch_extract": 40, "price_resolution_ms": 90},
        "warnings": [],
    }

    clear_category_attr_cache_for_tests()
    with patch("src.product_add.fetch_public_product", return_value=fake) as fetch_mock:
        with patch("src.product_add.find_connected_owner_for_item", return_value=None):
            with patch("src.product_add.client_for_store") as client_fn:
                mock_client = MagicMock()
                mock_client.get_category_attributes.return_value = LIVE_ATTR_SCHEMA
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
                        "https://www.daraz.pk/products/bag-i433265727.html",
                        ["store-a", "store-b"],
                        execute=False,
                    )

    assert fetch_mock.call_count == 1
    # Category schema fetched once per marketplace/category (shared cache)
    assert mock_client.get_category_attributes.call_count == 1
    assert result["needs_attention_count"] == 0
    for d in result["destinations"]:
        assert d["status"] != "NEEDS_ATTENTION"
        preview = d.get("draft_preview") or {}
        missing = (preview.get("validation_status") or {}).get("missing_required") or []
        errors = (preview.get("validation_status") or {}).get("errors") or []
        assert not any("color_family" in str(m) for m in missing)
        assert not any("short_description" in str(m) for m in missing)
        assert not any("category_missing" in str(e) for e in errors)
        timings = d.get("timings_ms") or {}
        assert "category_attributes_ms" in timings
        assert "attribute_resolution_ms" in timings
        skus = preview.get("Skus") or []
        colors = [s.get("saleProp", {}).get("color_family") for s in skus]
        assert colors == ["Black", "Pink", "Purple", "Green"]


def test_attribute_resolution_timing_present():
    draft = _bag_draft()
    report = resolve_required_category_attributes(draft, LIVE_ATTR_SCHEMA)
    assert "attribute_resolution_ms" in report["timings_ms"]
    assert report["timings_ms"]["attribute_resolution_ms"] >= 0
