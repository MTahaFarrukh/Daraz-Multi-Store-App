"""Phase A2 — probe isolation, mutation-free dry-run, supervised claims, brand diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.product_create import (
    create_probe_enabled,
    product_create_enabled,
    run_supervised_create,
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
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    return reset_repo_for_tests()


def _add_store(repo, wid: str, store_id: str) -> dict[str, Any]:
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


def _seed_product(
    repo,
    wid: str,
    store: dict[str, Any],
    *,
    item_id: str = "1001",
    image_url: str = "https://static-01.daraz.pk/p/abc.png",
) -> dict[str, Any]:
    product = repo.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_item_id": item_id,
            "title": "Probe Hardening Product",
            "title_en": "Probe Hardening Product",
            "primary_category_id": 10002730,
            "brand": "Nike",
            "status_raw": "Active",
            "description_en": "<p>Nice product</p>",
            "images_json": [
                {"url": image_url, "position": 0, "kind": "product"},
            ],
            "attributes_json": {
                "warranty_type": "No Warranty",
                "brand": "Nike",
                "short_description_en": "Short",
                "name": "Probe Hardening Product",
                "name_en": "Probe Hardening Product",
            },
            "short_description_en": "Short",
        }
    )
    repo.replace_product_variants(
        wid,
        product["id"],
        [
            {
                "workspace_id": wid,
                "store_id": store["id"],
                "product_id": product["id"],
                "daraz_sku_id": f"sku-{item_id}",
                "seller_sku": "SOURCE-SKU",
                "sale_props_json": {},
                "price": 500,
                "quantity": 3,
                "package_weight": 0.2,
                "package_length": 10,
                "package_width": 8,
                "package_height": 4,
            }
        ],
    )
    return product


SAMPLE_CATEGORY_ATTRS = {
    "data": [
        {"name": "name", "is_mandatory": 1, "attribute_type": "normal"},
        {"name": "name_en", "is_mandatory": 1, "attribute_type": "normal"},
        {"name": "brand", "is_mandatory": 1, "attribute_type": "normal"},
        {"name": "warranty_type", "is_mandatory": 1, "attribute_type": "normal"},
    ]
}


def _mutation_tracking_client(*, migrate_calls: list, create_calls: list, price_calls: list):
    class FakeClient:
        def get_category_attributes(self, *_a, **_k):
            return SAMPLE_CATEGORY_ATTRS

        def query_category_brands(self, **_k):
            return {
                "data": {
                    "module": [
                        {
                            "name": "No Brand",
                            "name_en": "No Brand",
                            "brand_id": 60147,
                            "global_identifier": "No Brand",
                        }
                    ],
                    "total_record": 1,
                }
            }

        def migrate_image(self, url: str):
            migrate_calls.append(("migrate_image", url))
            raise AssertionError("migrate_image must not run in dry-run")

        def migrate_images(self, urls: list):
            migrate_calls.append(("migrate_images", list(urls)))
            raise AssertionError("migrate_images must not run in dry-run")

        def create_product(self, xml: str):
            create_calls.append(xml)
            raise AssertionError("create_product must not run in dry-run")

        def update_price(self, xml: str):
            price_calls.append(xml)
            raise AssertionError("update_price must not run in dry-run")

        def get_product_item(self, *_a, **_k):
            return {"code": "0", "data": {}}

        def get_products(self, **_k):
            return {"code": "0", "data": {"products": []}}

    return FakeClient()


def test_probe_alone_does_not_enable_ordinary_create(monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "true")
    assert create_probe_enabled() is True
    assert product_create_enabled() is False


def test_ordinary_create_requires_allow_product_create(monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "true")
    assert product_create_enabled() is True
    assert create_probe_enabled() is False


def test_add_product_blocked_when_only_probe_enabled(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "true")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    from src.brand_resolve import clear_no_brand_cache_for_tests
    from src.product_add import add_product_from_connected

    clear_no_brand_cache_for_tests()
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(tenancy_env, wid, src)

    migrate_calls: list = []
    create_calls: list = []
    price_calls: list = []
    client = _mutation_tracking_client(
        migrate_calls=migrate_calls, create_calls=create_calls, price_calls=price_calls
    )
    monkeypatch.setattr("src.ops.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr("src.product_add.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr("src.product_fetch.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **_k: []
    )
    monkeypatch.setattr(
        "src.product_fetch.access_token_expires_soon", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        "src.product_add.fetch_connected_product",
        lambda *a, **k: {
            "product": {
                **product,
                "id": product["id"],
                "daraz_item_id": "1001",
                "store_id": src["id"],
            },
            "source_store": src,
            "timings_ms": {},
        },
    )

    out = add_product_from_connected(
        wid,
        "store_a",
        "1001",
        ["store_b"],
        execute=True,
    )
    assert out.get("product_create_enabled") is False
    assert create_calls == []
    assert price_calls == []
    # Dry validation may still resolve CDN without migrate; migrate must stay empty.
    assert migrate_calls == []
    assert not any(
        str((d or {}).get("creation_status") or "") in {"CREATED", "VERIFIED"}
        or str((d or {}).get("status") or "").lower().startswith("created")
        for d in (out.get("destinations") or [])
    )


def test_supervised_dry_run_never_mutates(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "true")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    from src.brand_resolve import clear_no_brand_cache_for_tests

    clear_no_brand_cache_for_tests()
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(tenancy_env, wid, src)

    migrate_calls: list = []
    create_calls: list = []
    price_calls: list = []
    client = _mutation_tracking_client(
        migrate_calls=migrate_calls, create_calls=create_calls, price_calls=price_calls
    )
    monkeypatch.setattr("src.ops.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr("src.product_add.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **_k: []
    )

    out = run_supervised_create(
        wid,
        source_product_id=str(product["id"]),
        destination_store_id="store_b",
        confirm=True,
        execute=False,
    )
    assert migrate_calls == []
    assert create_calls == []
    assert price_calls == []
    assert out["status"] in {"READY", "NOT_RUN"}


def test_supervised_dry_run_reports_non_cdn_without_migrate(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "true")
    from src.brand_resolve import clear_no_brand_cache_for_tests

    clear_no_brand_cache_for_tests()
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(
        tenancy_env,
        wid,
        src,
        image_url="https://example.com/external.jpg",
    )

    migrate_calls: list = []
    create_calls: list = []
    price_calls: list = []
    client = _mutation_tracking_client(
        migrate_calls=migrate_calls, create_calls=create_calls, price_calls=price_calls
    )
    monkeypatch.setattr("src.ops.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr("src.product_add.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **_k: []
    )

    out = run_supervised_create(
        wid,
        source_product_id=str(product["id"]),
        destination_store_id="store_b",
        confirm=True,
        execute=False,
    )
    assert migrate_calls == []
    assert create_calls == []
    assert out["status"] == "NOT_RUN"
    blob = str(out.get("reason") or "") + str(
        (out.get("validation") or {}).get("errors") or []
    )
    assert "image" in blob.lower() or "migration" in blob.lower()


def test_supervised_execute_uses_claim_and_blocks_concurrent(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "true")
    from src.brand_resolve import clear_no_brand_cache_for_tests
    from src.product_copy_mapping import acquire_create_claim

    clear_no_brand_cache_for_tests()
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    dst = _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(tenancy_env, wid, src)

    acquired = acquire_create_claim(
        wid,
        source_store_id=str(src["id"]),
        source_daraz_item_id="1001",
        destination_store_id=str(dst["id"]),
    )
    assert acquired.get("acquired") is True

    create_calls: list = []
    migrate_calls: list = []
    price_calls: list = []
    client = _mutation_tracking_client(
        migrate_calls=migrate_calls, create_calls=create_calls, price_calls=price_calls
    )
    # CDN reuse only — claim should block before create
    monkeypatch.setattr("src.ops.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr("src.product_add.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **_k: []
    )

    out = run_supervised_create(
        wid,
        source_product_id=str(product["id"]),
        destination_store_id="store_b",
        confirm=True,
        execute=True,
    )
    assert create_calls == []
    assert out["status"] == "NOT_RUN"
    reason = str(out.get("reason") or "").lower()
    assert "claim" in reason or "concurrent" in reason or "reconcil" in reason


def test_supervised_execute_records_attempt(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "true")
    from src.brand_resolve import clear_no_brand_cache_for_tests

    clear_no_brand_cache_for_tests()
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(tenancy_env, wid, src)

    create_calls: list = []

    class SuccessClient:
        def get_category_attributes(self, *_a, **_k):
            return SAMPLE_CATEGORY_ATTRS

        def query_category_brands(self, **_k):
            return {
                "data": {
                    "module": [{"name": "No Brand", "brand_id": 60147}],
                    "total_record": 1,
                }
            }

        def migrate_image(self, url: str):
            return {"data": {"image": {"url": url}}}

        def create_product(self, xml: str):
            create_calls.append(xml)
            assert "<brand>No Brand</brand>" in xml
            assert "Nike" not in xml
            return {
                "code": "0",
                "data": {
                    "item_id": "999001",
                    "sku_list": [{"seller_sku": "MTF-X", "shop_sku": "s1"}],
                },
            }

        def update_price(self, xml: str):
            return {"code": "0"}

        def get_product_item(self, item_id):
            return {
                "code": "0",
                "data": {
                    "item_id": item_id,
                    "skus": [
                        {
                            "SellerSku": "MTF-X",
                            "price": 500,
                            "quantity": 3,
                        }
                    ],
                },
            }

        def get_products(self, **_k):
            return {"code": "0", "data": {"products": []}}

    client = SuccessClient()
    monkeypatch.setattr("src.ops.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr("src.product_add.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **_k: []
    )

    out = run_supervised_create(
        wid,
        source_product_id=str(product["id"]),
        destination_store_id="store_b",
        confirm=True,
        execute=True,
    )
    assert create_calls, "CreateProduct should run when execute=true under probe"
    assert out.get("attempt_id"), "must record durable attempt for reconciliation"
    assert out.get("item_id") == "999001" or out["status"] in {
        "PROVEN",
        "NEEDS_ATTENTION",
    }


def test_brand_diagnostics_found(tenancy_env, monkeypatch):
    from src.brand_diagnostics import diagnose_category_no_brand
    from src.brand_resolve import clear_no_brand_cache_for_tests

    clear_no_brand_cache_for_tests()
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "store_a")

    class BrandClient:
        def query_category_brands(self, **_k):
            return {
                "data": {
                    "module": [
                        {
                            "name": "No Brand",
                            "brand_id": 60147,
                            "global_identifier": "No Brand",
                        }
                    ],
                    "total_record": 1,
                }
            }

        def migrate_image(self, *_a, **_k):
            raise AssertionError("diagnostics must not migrate")

        def create_product(self, *_a, **_k):
            raise AssertionError("diagnostics must not create")

    monkeypatch.setattr(
        "src.ops.client_for_store", lambda *_a, **_k: BrandClient()
    )
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **_k: []
    )

    out = diagnose_category_no_brand(
        wid, store_ref="store_a", primary_category_id=10002730
    )
    assert out["status"] == "NO_BRAND"
    assert out["no_brand_found"] is True
    assert out["brand"] == "No Brand"
    assert out["brand_id"] == 60147


def test_brand_diagnostics_unverified_when_incomplete(tenancy_env, monkeypatch):
    from src.brand_diagnostics import diagnose_category_no_brand
    from src.brand_resolve import clear_no_brand_cache_for_tests

    clear_no_brand_cache_for_tests()
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "store_a")

    class IncompleteClient:
        def query_category_brands(self, **kwargs):
            return {
                "data": {
                    "module": [
                        {
                            "name": f"Brand-{kwargs.get('start_row', 0)}",
                            "brand_id": 1,
                        }
                    ],
                    "total_record": 500000,
                }
            }

    monkeypatch.setattr(
        "src.ops.client_for_store", lambda *_a, **_k: IncompleteClient()
    )
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **_k: []
    )

    out = diagnose_category_no_brand(
        wid, store_ref="store_a", primary_category_id=9
    )
    assert out["status"] == "UNVERIFIED"
    assert out["no_brand_found"] is False
    assert out["brand"] is None
    assert out["brand_id"] is None
