"""Regression: confirm selectable Daraz No Brand despite weak name filter."""

from __future__ import annotations

from src.brand_resolve import (
    clear_no_brand_cache_for_tests,
    find_no_brand_row,
    is_no_brand_label,
    resolve_brand_for_category,
    resolve_destination_no_brand,
)
from src.product_create_payload import build_create_product_xml


def test_is_no_brand_label_normalization():
    assert is_no_brand_label("No Brand") is True
    assert is_no_brand_label("  no   brand ") is True
    assert is_no_brand_label("NO_BRAND") is True
    assert is_no_brand_label("No-Brand") is True
    assert is_no_brand_label("NoBrand") is True
    assert is_no_brand_label("No Branding Co") is False
    assert is_no_brand_label("Brand") is False
    assert is_no_brand_label("") is False
    assert is_no_brand_label(None) is False


def test_exact_no_brand_option_selected():
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {
            "data": {
                "module": [
                    {"name": "Acme", "brand_id": 9},
                    {"name": "No Brand", "brand_id": 60147, "global_identifier": "No Brand"},
                ],
                "total_record": 2,
            }
        }

    res = resolve_destination_no_brand(primary_category_id=10001, query_brands=query)
    assert res["status"] == "NO_BRAND"
    assert res["brand"] == "No Brand"
    assert res["brand_id"] == 60147
    assert res["used_no_brand"] is True


def test_case_and_whitespace_variants_match():
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {
            "data": {
                "module": [
                    {
                        "name": "  no brand ",
                        "name_en": "No Brand",
                        "global_identifier": "NO_BRAND",
                        "brand_id": 7,
                    }
                ]
            }
        }

    res = resolve_brand_for_category(
        source_brand=None, primary_category_id=1, query_brands=query
    )
    assert res["status"] == "NO_BRAND"
    assert is_no_brand_label(res["brand"])
    assert res["brand_id"] == 7


def test_similar_brand_names_not_matched():
    clear_no_brand_cache_for_tests()
    payload = {
        "data": {
            "module": [
                {"name": "No Branding Co", "brand_id": 1},
                {"name": "Brandless", "brand_id": 2},
                {"name": "No Brandy", "brand_id": 3},
            ],
            "total_record": 3,
        }
    }
    assert find_no_brand_row(payload) is None

    def query(**kwargs):
        return payload

    res = resolve_destination_no_brand(primary_category_id=2, query_brands=query)
    assert res["status"] == "UNRESOLVED"
    assert res["reason"] == "no_brand_unavailable"
    assert res["brand"] is None
    assert res["brand_id"] is None


def test_no_brand_genuinely_absent():
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {
            "data": {
                "module": [
                    {"name": "Nike", "brand_id": 10},
                    {"name": "Adidas", "brand_id": 11},
                ],
                "total_record": 2,
            }
        }

    res = resolve_brand_for_category(
        source_brand="Nike", primary_category_id=100, query_brands=query
    )
    assert res["status"] == "UNRESOLVED"
    assert res["reason"] == "no_brand_unavailable"
    assert res["brand"] is None


def test_malformed_and_empty_lookup_responses():
    clear_no_brand_cache_for_tests()

    def empty(**kwargs):
        return {"data": {"module": [], "total_record": 0}}

    res = resolve_destination_no_brand(primary_category_id=3, query_brands=empty)
    assert res["status"] == "UNRESOLVED"
    assert res["brand"] is None

    clear_no_brand_cache_for_tests()

    def malformed(**kwargs):
        return {"data": "not-a-dict"}

    res2 = resolve_destination_no_brand(primary_category_id=4, query_brands=malformed)
    assert res2["status"] == "UNRESOLVED"

    clear_no_brand_cache_for_tests()

    def raises(**kwargs):
        raise RuntimeError("upstream")

    res3 = resolve_destination_no_brand(primary_category_id=5, query_brands=raises)
    assert res3["status"] == "UNRESOLVED"
    assert res3["lookup"]["stop_reason"] == "lookup_error"
    assert res3["lookup"]["error_type"] == "RuntimeError"


def test_ineffective_name_filter_then_find_on_later_page():
    """Reproduce Daraz PK: name=No Brand ignored; No Brand exists deeper in catalog."""
    clear_no_brand_cache_for_tests()
    calls: list[dict] = []

    first_page = [
        {"name": "test123123", "global_identifier": "test123123", "brand_id": 23885},
        {"name": "2K Games", "brand_id": 23886},
        {"name": "Adobe", "brand_id": 23902},
    ]
    no_brand_page = [
        {"name": "Nokia", "brand_id": 500},
        {
            "name": "No Brand",
            "name_en": "No Brand",
            "global_identifier": "No Brand",
            "brand_id": 60147,
        },
        {"name": "North Face", "brand_id": 501},
    ]

    def query(**kwargs):
        calls.append(dict(kwargs))
        start = int(kwargs.get("start_row") or 0)
        name = kwargs.get("name")
        # Name filter ignored — always returns first page (production spike behavior).
        if name:
            return {
                "data": {
                    "module": first_page,
                    "total_record": 600,
                    "start_row": 0,
                }
            }
        if start >= 400:
            return {
                "data": {
                    "module": no_brand_page,
                    "total_record": 600,
                    "start_row": start,
                }
            }
        # Pages before the No Brand window.
        return {
            "data": {
                "module": [
                    {"name": f"Brand-{start}", "brand_id": start + 1},
                    {"name": f"Brand-{start + 1}", "brand_id": start + 2},
                ],
                "total_record": 600,
                "start_row": start,
            }
        }

    res = resolve_destination_no_brand(
        primary_category_id=10002730,
        query_brands=query,
        page_size=50,
    )
    assert res["status"] == "NO_BRAND"
    assert res["brand"] == "No Brand"
    assert res["brand_id"] == 60147
    assert res["lookup"]["name_filter_effective"] is False
    assert any(c.get("name") for c in calls)
    assert any((c.get("start_row") or 0) >= 400 for c in calls if not c.get("name"))


def test_final_payload_uses_verified_brand_name_not_source():
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {
            "data": {
                "module": [
                    {"name": "No Brand", "brand_id": 60147},
                ]
            }
        }

    resolved = resolve_brand_for_category(
        source_brand="Nike",
        primary_category_id=9,
        query_brands=query,
    )
    draft = {
        "product": {
            "title": "Demo",
            "primary_category_id": 9,
            "brand": resolved["brand"],
            "attributes": {"brand": resolved["brand"], "name": "Demo"},
        },
        "media": {"resolved_images": ["https://static-01.daraz.pk/p/x.jpg"]},
        "variants": [
            {
                "seller_sku": "MTF-DEMO-DEFAULT",
                "price": 100,
                "quantity": 1,
                "package_weight": 0.1,
                "package_length": 8,
                "package_width": 10,
                "package_height": 5,
            }
        ],
        "brand_resolution": resolved,
    }
    xml = build_create_product_xml(draft)
    assert "<brand>No Brand</brand>" in xml
    assert "Nike" not in xml
    assert resolved["brand_id"] == 60147


def test_global_identifier_only_row_still_resolves():
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {
            "data": {
                "module": [
                    {"global_identifier": "NO_BRAND", "brand_id": 42},
                ]
            }
        }

    res = resolve_destination_no_brand(primary_category_id=8, query_brands=query)
    assert res["status"] == "NO_BRAND"
    assert is_no_brand_label(res["brand"])
    assert res["brand_id"] == 42


def test_never_invents_brand_id_when_row_omits_it():
    """Verified name is enough; brand_id must come from Daraz row or stay None."""
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {
            "data": {
                "module": [
                    {"name": "No Brand", "name_en": "No Brand"},
                ],
                "total_record": 1,
            }
        }

    res = resolve_destination_no_brand(primary_category_id=11, query_brands=query)
    assert res["status"] == "NO_BRAND"
    assert res["brand"] == "No Brand"
    assert res["brand_id"] is None


def test_never_hardcodes_fallback_brand_id_on_miss():
    clear_no_brand_cache_for_tests()

    def query(**kwargs):
        return {
            "data": {
                "module": [{"name": "Acme Co", "brand_id": 60147}],
                "total_record": 1,
            }
        }

    res = resolve_destination_no_brand(primary_category_id=12, query_brands=query)
    assert res["status"] == "UNRESOLVED"
    assert res["brand"] is None
    assert res["brand_id"] is None
    assert res["reason"] == "no_brand_unavailable"
