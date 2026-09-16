"""Direct Copy Product — Item ID fetch without catalog sync."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.auth import issue_test_token
from src.daraz_api import DarazApiError
from src.db import reset_repo_for_tests
from src.product_fetch import (
    ProductFetchError,
    clone_draft_from_connected,
    ensure_product_detail,
    fetch_connected_product,
    is_detail_fresh,
)
from src.product_sync import sync_store_products


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
    monkeypatch.setenv("ALLOW_LEGACY_DATA_IMPORT", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    return reset_repo_for_tests()


@pytest.fixture()
def client(tenancy_env):
    from src.app import app

    return TestClient(app)


def _auth(user_id: str, email: str = "a@example.com") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_test_token(user_id, email=email)}"}


def _bootstrap(client: TestClient, user_id: str, email: str) -> str:
    headers = _auth(user_id, email)
    res = client.post("/api/bootstrap", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["workspace"]["id"]


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


SAMPLE_ITEM = {
    "item_id": 123456789,
    "primary_category": 10002730,
    "status": "Active",
    "attributes": {
        "name": "Glow Dumpling",
        "name_en": "Glow Dumpling",
        "brand": "No Brand",
        "description_en": "<p>Nice</p>",
        "short_description_en": "Short",
        "warranty_type": "No Warranty",
        "package_content": "1 pc",
    },
    "variation": {"Variation1": {"name": "color_family", "options": ["Glow"]}},
    "images": ["https://static-01.daraz.pk/p/abc.png"],
    "skus": [
        {
            "SkuId": 111,
            "SellerSku": "SOURCE-SKU-1",
            "price": 499,
            "quantity": 20,
            "package_weight": 0.25,
            "package_length": 12,
            "package_width": 8,
            "package_height": 5,
            "saleProp": {"color_family": "Glow"},
        }
    ],
}


def _fake_client(item: dict[str, Any] | None = None, *, error: Exception | None = None):
    client = MagicMock()
    if error:
        client.get_product_item.side_effect = error
    else:
        client.get_product_item.return_value = {"code": "0", "data": item or SAMPLE_ITEM}

    def _products_get(**kwargs):
        raise AssertionError("/products/get must not be called during direct fetch")

    client.get_products.side_effect = _products_get
    return client


def test_direct_fetch_without_catalog_sync(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    fake = _fake_client()
    monkeypatch.setattr("src.product_fetch.client_for_store", lambda store: fake)
    monkeypatch.setattr("src.product_fetch.access_token_expires_soon", lambda *a, **k: False)

    # Empty warehouse — no prior Sync Products
    assert tenancy_env.list_daraz_products(wid, {"page": 1, "page_size": 10})["total"] == 0

    result = fetch_connected_product(
        wid, source_store_id="store_a", daraz_item_id="123456789"
    )
    fake.get_product_item.assert_called_once_with("123456789")
    fake.get_products.assert_not_called()
    assert result["api_calls"]["product_item_get"] == 1
    assert result["api_calls"]["products_get"] == 0
    assert result["product"]["detail_complete"] is True
    assert result["product"]["daraz_item_id"] == "123456789"
    assert len(result["variants"]) == 1
    assert result["variants"][0]["seller_sku"] == "SOURCE-SKU-1"  # warehouse keeps source
    assert result["variants"][0]["package_weight"] == 0.25
    # upserted locally
    assert tenancy_env.get_daraz_product_by_item_id(wid, src["id"], "123456789")


def test_clone_draft_from_connected_generates_mtf(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    fake = _fake_client()
    monkeypatch.setattr("src.product_fetch.client_for_store", lambda store: fake)
    monkeypatch.setattr("src.product_fetch.access_token_expires_soon", lambda *a, **k: False)

    result = clone_draft_from_connected(
        wid,
        source_store_id="store_a",
        daraz_item_id="123456789",
        destination_store_id="store_b",
    )
    draft = result["draft"]
    v0 = draft["variants"][0]
    assert v0["seller_sku"].startswith("MTF-")
    assert v0["seller_sku"] != "SOURCE-SKU-1"
    assert v0["source_seller_sku"] == "SOURCE-SKU-1"
    assert v0["quantity"] == 1
    assert v0["package_weight"] == 0.25
    assert v0["package_length"] == 12
    assert result["api_calls"]["products_get"] == 0
    assert "timings_ms" in result


def test_same_store_clone_blocked(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "store_a")
    fake = _fake_client()
    monkeypatch.setattr("src.product_fetch.client_for_store", lambda store: fake)
    monkeypatch.setattr("src.product_fetch.access_token_expires_soon", lambda *a, **k: False)
    with pytest.raises(ValueError, match="differ"):
        clone_draft_from_connected(
            wid,
            source_store_id="store_a",
            daraz_item_id="123456789",
            destination_store_id="store_a",
        )


def test_cross_workspace_store_blocked(tenancy_env, monkeypatch):
    wid_a = tenancy_env.create_workspace_with_owner("u1", "A")["workspace"]["id"]
    wid_b = tenancy_env.create_workspace_with_owner("u2", "B")["workspace"]["id"]
    _add_store(tenancy_env, wid_a, "store_a")
    _add_store(tenancy_env, wid_b, "store_b")
    with pytest.raises(ProductFetchError, match="not found"):
        fetch_connected_product(
            wid_b, source_store_id="store_a", daraz_item_id="123456789"
        )


def test_item_not_found_clear_error(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    _add_store(tenancy_env, wid, "store_a")
    fake = _fake_client(
        error=DarazApiError("Product not found", code="NotFound", payload={"code": "NotFound"})
    )
    monkeypatch.setattr("src.product_fetch.client_for_store", lambda store: fake)
    monkeypatch.setattr("src.product_fetch.access_token_expires_soon", lambda *a, **k: False)
    with pytest.raises(ProductFetchError, match="not found"):
        fetch_connected_product(
            wid, source_store_id="store_a", daraz_item_id="999"
        )


def test_catalog_sync_no_n_plus_one_detail(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "store_a")
    client = MagicMock()
    # Simulate 3 list pages worth of products in one page
    products = []
    for i in range(60):
        products.append(
            {
                "item_id": 1000 + i,
                "primary_category": 1,
                "status": "Active",
                "attributes": {"name": f"P{i}", "brand": "No Brand"},
                "skus": [{"SkuId": 2000 + i, "SellerSku": f"S{i}", "price": 10, "quantity": 1}],
                "images": [],
                "variation": {"Variation1": {"name": "color_family", "options": ["A"]}},
            }
        )
    client.get_products.side_effect = [
        {"data": {"products": products[:50]}},
        {"data": {"products": products[50:]}},
    ]

    def _boom(*_a, **_k):
        raise AssertionError("get_product_item must not run when fetch_details=False")

    client.get_product_item.side_effect = _boom
    monkeypatch.setattr("src.product_sync.client_for_store", lambda s: client)
    monkeypatch.setattr("src.product_sync.access_token_expires_soon", lambda *a, **k: False)

    result = sync_store_products(wid, store, fetch_details=False)
    assert result["products_upserted"] == 60
    assert result["detail_fetched"] == 0
    client.get_product_item.assert_not_called()
    sample = tenancy_env.get_daraz_product_by_item_id(wid, store["id"], "1000")
    assert sample["detail_complete"] is False


def test_lazy_hydrate_incomplete_then_skip_fresh(tenancy_env, monkeypatch):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "store_a")
    product = tenancy_env.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_item_id": "123456789",
            "title": "Glow Dumpling",
            "detail_complete": False,
        }
    )
    fake = _fake_client()
    monkeypatch.setattr("src.product_fetch.client_for_store", lambda s: fake)
    monkeypatch.setattr("src.product_fetch.access_token_expires_soon", lambda *a, **k: False)

    first = ensure_product_detail(wid, product["id"])
    assert first["hydrated"] is True
    assert fake.get_product_item.call_count == 1
    assert is_detail_fresh(first["product"])

    second = ensure_product_detail(wid, product["id"])
    assert second["hydrated"] is False
    assert fake.get_product_item.call_count == 1  # no refetch


def test_api_from_connected(client, tenancy_env, monkeypatch):
    wid = _bootstrap(client, "user-a", "a@example.com")
    _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    fake = _fake_client()
    monkeypatch.setattr("src.product_fetch.client_for_store", lambda store: fake)
    monkeypatch.setattr("src.product_fetch.access_token_expires_soon", lambda *a, **k: False)

    res = client.post(
        "/api/products/clone-draft/from-connected",
        headers=headers,
        json={
            "source_store_id": "store_a",
            "daraz_item_id": "123456789",
            "destination_store_id": "store_b",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["draft"]["variants"][0]["seller_sku"].startswith("MTF-")
    assert body["api_calls"]["products_get"] == 0
    assert body["api_calls"]["product_item_get"] == 1
