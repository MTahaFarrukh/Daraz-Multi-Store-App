"""Phase 4B local product warehouse — repo persistence (schema + repo only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / ".token_key"
    key_path.write_text(key, encoding="utf-8")
    monkeypatch.setenv("AUTH_TEST_MODE", "true")
    monkeypatch.setenv("TENANCY_REPO", "memory")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    return reset_repo_for_tests()


def _workspace(repo, user_id: str = "u1", name: str = "W") -> str:
    return repo.create_workspace_with_owner(user_id, name)["workspace"]["id"]


def _add_store(repo, workspace_id: str, store_id: str, account: str) -> dict[str, Any]:
    return repo.upsert_store(
        workspace_id,
        {
            "store_id": store_id,
            "account": account,
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


def _seed_product(
    repo, workspace_id: str, store: dict[str, Any], daraz_item_id: str, **extra
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "store_id": store["id"],
        "daraz_item_id": daraz_item_id,
        "title": extra.get("title", f"Product {daraz_item_id}"),
        "primary_category_id": extra.get("primary_category_id", 1000),
        "primary_category_name": extra.get("primary_category_name", "Audio"),
        "brand": extra.get("brand", "No Brand"),
        "status_raw": extra.get("status_raw", "active"),
    }
    payload.update(extra)
    return repo.upsert_daraz_product(payload)


def _seed_variant(
    repo,
    workspace_id: str,
    store: dict[str, Any],
    product: dict[str, Any],
    daraz_sku_id: str,
    **extra,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "store_id": store["id"],
        "product_id": product["id"],
        "daraz_sku_id": daraz_sku_id,
        "seller_sku": extra.get("seller_sku", f"MTF-{daraz_sku_id}"),
        "shop_sku": extra.get("shop_sku", f"SHOP-{daraz_sku_id}"),
        "price": extra.get("price", 1999.5),
        "quantity": extra.get("quantity", 7),
    }
    payload.update(extra)
    return repo.upsert_daraz_product_variant(payload)


def test_upsert_product_idempotent(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")

    first = _seed_product(repo, wid, store, "item-1", title="Original Title")
    second = _seed_product(repo, wid, store, "item-1", title="Updated Title")

    assert first["id"] == second["id"]
    assert second["title"] == "Updated Title"
    assert second["created_at"] == first["created_at"]
    assert repo.list_daraz_products(wid, {})["total"] == 1
    # JSONB columns fall back to their SQL defaults.
    assert second["attributes_json"] == {}
    assert second["variation_json"] == {}
    assert second["images_json"] == []
    assert second["market_images_json"] == []


def test_upsert_product_stores_json_payloads(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")

    product = _seed_product(
        repo,
        wid,
        store,
        "item-json",
        attributes_json={"name": "Earbuds", "warranty_type": "No Warranty"},
        images_json=[{"url": "https://img/1.jpg", "position": 0, "kind": "main"}],
        market_images_json=["https://img/market1.jpg"],
        video_ref="video-abc",
    )

    assert product["attributes_json"]["warranty_type"] == "No Warranty"
    assert product["images_json"][0]["kind"] == "main"
    assert product["market_images_json"] == ["https://img/market1.jpg"]
    assert product["video_ref"] == "video-abc"


def test_same_item_id_across_two_stores_isolated(repo) -> None:
    wid = _workspace(repo)
    s1 = _add_store(repo, wid, "s1", "a@s.com")
    s2 = _add_store(repo, wid, "s2", "b@s.com")

    p1 = _seed_product(repo, wid, s1, "shared-item")
    p2 = _seed_product(repo, wid, s2, "shared-item")

    assert p1["id"] != p2["id"]
    assert repo.list_daraz_products(wid, {})["total"] == 2
    only_s1 = repo.list_daraz_products(wid, {"store_slugs": ["s1"]})
    assert only_s1["total"] == 1
    assert only_s1["items"][0]["id"] == p1["id"]


def test_variant_same_sku_id_across_two_stores_isolated(repo) -> None:
    wid = _workspace(repo)
    s1 = _add_store(repo, wid, "s1", "a@s.com")
    s2 = _add_store(repo, wid, "s2", "b@s.com")
    p1 = _seed_product(repo, wid, s1, "item-1")
    p2 = _seed_product(repo, wid, s2, "item-1")

    v1 = _seed_variant(repo, wid, s1, p1, "sku-1")
    v2 = _seed_variant(repo, wid, s2, p2, "sku-1")

    assert v1["id"] != v2["id"]
    assert len(repo.list_daraz_product_variants(wid, p1["id"])) == 1
    assert len(repo.list_daraz_product_variants(wid, p2["id"])) == 1


def test_workspace_isolation_on_get(repo) -> None:
    wid_a = _workspace(repo, "user-a", "A")
    wid_b = _workspace(repo, "user-b", "B")
    store_b = _add_store(repo, wid_b, "s-b", "b@s.com")
    product_b = _seed_product(repo, wid_b, store_b, "item-b")

    assert repo.get_daraz_product(wid_b, product_b["id"]) is not None
    assert repo.get_daraz_product(wid_a, product_b["id"]) is None
    assert repo.list_daraz_products(wid_a, {})["total"] == 0
    assert repo.list_daraz_product_variants(wid_a, product_b["id"]) == []


def test_list_products_pagination(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")
    for i in range(5):
        _seed_product(repo, wid, store, f"item-{i}", title=f"Widget {i}")

    page1 = repo.list_daraz_products(wid, {"page": 1, "page_size": 2})
    assert page1["total"] == 5
    assert page1["page"] == 1
    assert page1["page_size"] == 2
    assert len(page1["items"]) == 2

    page3 = repo.list_daraz_products(wid, {"page": 3, "page_size": 2})
    assert len(page3["items"]) == 1

    seen = set()
    for page in (1, 2, 3):
        for item in repo.list_daraz_products(wid, {"page": page, "page_size": 2})[
            "items"
        ]:
            seen.add(item["daraz_item_id"])
    assert seen == {f"item-{i}" for i in range(5)}


def test_list_products_search(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")
    earbuds = _seed_product(
        repo, wid, store, "item-1", title="Wireless Earbuds Pro", brand="Soundcore"
    )
    bottle = _seed_product(
        repo, wid, store, "item-2", title="Steel Water Bottle", brand="Milton"
    )
    _seed_variant(repo, wid, store, bottle, "sku-b", seller_sku="MTF-BOTTLE-1L")

    by_title = repo.list_daraz_products(wid, {"search": "earbuds"})
    assert [i["id"] for i in by_title["items"]] == [earbuds["id"]]

    by_brand = repo.list_daraz_products(wid, {"search": "milton"})
    assert [i["id"] for i in by_brand["items"]] == [bottle["id"]]

    by_item_id = repo.list_daraz_products(wid, {"search": "item-1"})
    assert [i["id"] for i in by_item_id["items"]] == [earbuds["id"]]

    by_seller_sku = repo.list_daraz_products(wid, {"search": "bottle-1l"})
    assert [i["id"] for i in by_seller_sku["items"]] == [bottle["id"]]

    assert repo.list_daraz_products(wid, {"search": "no-such-thing"})["total"] == 0


def test_list_products_status_and_category_filters(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")
    active = _seed_product(
        repo, wid, store, "item-1", status_raw="Active", primary_category_id=1000
    )
    inactive = _seed_product(
        repo, wid, store, "item-2", status_raw="inactive", primary_category_id=2000
    )

    only_active = repo.list_daraz_products(wid, {"status": "active"})
    assert [i["id"] for i in only_active["items"]] == [active["id"]]

    both = repo.list_daraz_products(wid, {"status": ["active", "inactive"]})
    assert both["total"] == 2

    by_category = repo.list_daraz_products(wid, {"category_id": 2000})
    assert [i["id"] for i in by_category["items"]] == [inactive["id"]]

    assert repo.list_daraz_products(wid, {"category_id": 9999})["total"] == 0


def test_list_products_sort_by_title(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")
    _seed_product(repo, wid, store, "item-1", title="Zebra Case")
    _seed_product(repo, wid, store, "item-2", title="Alpha Charger")

    asc = repo.list_daraz_products(wid, {"sort": "title_asc"})
    assert [i["title"] for i in asc["items"]] == ["Alpha Charger", "Zebra Case"]
    desc = repo.list_daraz_products(wid, {"sort": "title_desc"})
    assert [i["title"] for i in desc["items"]] == ["Zebra Case", "Alpha Charger"]


def test_list_products_includes_variants_count(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")
    product = _seed_product(repo, wid, store, "item-1")
    _seed_variant(repo, wid, store, product, "sku-1")
    _seed_variant(repo, wid, store, product, "sku-2")

    listed = repo.list_daraz_products(wid, {})
    assert listed["items"][0]["variants_count"] == 2


def test_upsert_variant_idempotent(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")
    product = _seed_product(repo, wid, store, "item-1")

    first = _seed_variant(repo, wid, store, product, "sku-1", quantity=5)
    second = _seed_variant(repo, wid, store, product, "sku-1", quantity=9)

    assert first["id"] == second["id"]
    assert second["quantity"] == 9
    assert second["price"] == 1999.5
    assert second["sale_props_json"] == {}
    assert second["images_json"] == []
    assert len(repo.list_daraz_product_variants(wid, product["id"])) == 1


def test_replace_product_variants(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")
    product = _seed_product(repo, wid, store, "item-1")
    keep = _seed_variant(repo, wid, store, product, "sku-keep")
    _seed_variant(repo, wid, store, product, "sku-stale")

    replaced = repo.replace_product_variants(
        wid,
        product["id"],
        [
            {"daraz_sku_id": "sku-keep", "seller_sku": "MTF-KEEP", "quantity": 3},
            {"daraz_sku_id": "sku-new", "seller_sku": "MTF-NEW", "quantity": 4},
        ],
    )

    by_sku = {v["daraz_sku_id"]: v for v in replaced}
    assert set(by_sku) == {"sku-keep", "sku-new"}
    assert by_sku["sku-keep"]["id"] == keep["id"]
    assert by_sku["sku-keep"]["quantity"] == 3
    assert by_sku["sku-new"]["store_id"] == store["id"]


def test_replace_product_variants_rejects_foreign_workspace(repo) -> None:
    wid_a = _workspace(repo, "user-a", "A")
    wid_b = _workspace(repo, "user-b", "B")
    store_b = _add_store(repo, wid_b, "s-b", "b@s.com")
    product_b = _seed_product(repo, wid_b, store_b, "item-b")

    with pytest.raises(ValueError):
        repo.replace_product_variants(
            wid_a, product_b["id"], [{"daraz_sku_id": "sku-x"}]
        )


def test_product_defaults_missing_row_returns_nulls(repo) -> None:
    wid = _workspace(repo)
    defaults = repo.get_product_defaults(wid)

    assert defaults["workspace_id"] == wid
    assert defaults["default_package_weight"] is None
    assert defaults["default_package_length"] is None
    assert defaults["default_package_width"] is None
    assert defaults["default_package_height"] is None
    assert defaults["default_initial_quantity"] == 1
    assert defaults["sku_prefix"] == "MTF-"
    assert defaults["updated_at"] is None


def test_product_defaults_upsert_and_partial_merge(repo) -> None:
    wid = _workspace(repo)

    saved = repo.upsert_product_defaults(
        wid,
        {
            "default_package_weight": 0.5,
            "default_package_length": 12,
            "default_package_width": 8,
            "default_package_height": 4,
            "default_initial_quantity": 3,
            "sku_prefix": "ACME-",
        },
    )
    assert saved["default_package_weight"] == 0.5
    assert saved["default_initial_quantity"] == 3
    assert saved["sku_prefix"] == "ACME-"
    assert saved["updated_at"] is not None
    assert repo.get_product_defaults(wid)["sku_prefix"] == "ACME-"

    merged = repo.upsert_product_defaults(wid, {"default_package_weight": 1.25})
    assert merged["default_package_weight"] == 1.25
    assert merged["default_package_length"] == 12
    assert merged["default_initial_quantity"] == 3
    assert merged["sku_prefix"] == "ACME-"


def test_product_defaults_isolated_per_workspace(repo) -> None:
    wid_a = _workspace(repo, "user-a", "A")
    wid_b = _workspace(repo, "user-b", "B")
    repo.upsert_product_defaults(wid_a, {"sku_prefix": "A-"})

    assert repo.get_product_defaults(wid_a)["sku_prefix"] == "A-"
    assert repo.get_product_defaults(wid_b)["sku_prefix"] == "MTF-"


def test_list_destination_seller_skus(repo) -> None:
    wid = _workspace(repo)
    s1 = _add_store(repo, wid, "s1", "a@s.com")
    s2 = _add_store(repo, wid, "s2", "b@s.com")
    p1 = _seed_product(repo, wid, s1, "item-1")
    p2 = _seed_product(repo, wid, s2, "item-2")
    _seed_variant(repo, wid, s1, p1, "sku-1", seller_sku="MTF-A")
    _seed_variant(repo, wid, s1, p1, "sku-2", seller_sku="MTF-B")
    _seed_variant(repo, wid, s1, p1, "sku-3", seller_sku=None)
    _seed_variant(repo, wid, s2, p2, "sku-4", seller_sku="MTF-C")

    assert repo.list_destination_seller_skus(wid, s1["id"]) == {"MTF-A", "MTF-B"}
    assert repo.list_destination_seller_skus(wid, s2["id"]) == {"MTF-C"}


def test_find_possible_product_duplicates_soft_match(repo) -> None:
    wid = _workspace(repo)
    store = _add_store(repo, wid, "s1", "a@s.com")
    earbuds = _seed_product(
        repo,
        wid,
        store,
        "item-1",
        title="Wireless Bluetooth Earbuds Pro Black",
        primary_category_id=1000,
    )
    _seed_product(
        repo,
        wid,
        store,
        "item-2",
        title="Stainless Steel Water Bottle 1L",
        primary_category_id=2000,
    )

    exact = repo.find_possible_product_duplicates(
        wid,
        store["id"],
        title="wireless bluetooth earbuds pro black!",
        category_id=1000,
    )
    assert [m["id"] for m in exact] == [earbuds["id"]]
    assert exact[0]["match_reason"] == "title_exact"
    assert exact[0]["match_score"] == 1.0
    assert exact[0]["same_category"] is True

    similar = repo.find_possible_product_duplicates(
        wid, store["id"], title="Wireless Bluetooth Earbuds Pro", category_id=1000
    )
    assert [m["id"] for m in similar] == [earbuds["id"]]
    assert similar[0]["match_reason"] == "title_similar"
    assert 0.6 <= similar[0]["match_score"] < 1.0

    # Weak title overlap only counts as a duplicate within the same category.
    weak_same_category = repo.find_possible_product_duplicates(
        wid, store["id"], title="Wireless Earbuds", category_id=1000
    )
    assert [m["match_reason"] for m in weak_same_category] == [
        "category_title_partial"
    ]
    assert (
        repo.find_possible_product_duplicates(
            wid, store["id"], title="Wireless Earbuds"
        )
        == []
    )

    assert (
        repo.find_possible_product_duplicates(
            wid, store["id"], title="Completely Unrelated Kitchen Sponge"
        )
        == []
    )


def test_find_possible_product_duplicates_scoping(repo) -> None:
    wid = _workspace(repo)
    other_wid = _workspace(repo, "user-b", "B")
    s1 = _add_store(repo, wid, "s1", "a@s.com")
    s2 = _add_store(repo, wid, "s2", "b@s.com")
    other_store = _add_store(repo, other_wid, "s-other", "c@s.com")

    title = "Wireless Bluetooth Earbuds Pro Black"
    p1 = _seed_product(repo, wid, s1, "item-1", title=title)
    _seed_product(repo, wid, s2, "item-1", title=title)
    _seed_product(repo, other_wid, other_store, "item-1", title=title)

    in_s1 = repo.find_possible_product_duplicates(wid, s1["id"], title=title)
    assert [m["id"] for m in in_s1] == [p1["id"]]

    excluded = repo.find_possible_product_duplicates(
        wid, s1["id"], title=title, exclude_product_id=p1["id"]
    )
    assert excluded == []
