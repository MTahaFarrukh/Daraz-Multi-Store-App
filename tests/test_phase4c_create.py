"""Phase 4C tests: image poll contract, CDN reuse, category/brand, payload, create gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.auth import issue_test_token
from src.brand_resolve import resolve_brand_for_category
from src.category_validate import validate_draft_against_category
from src.db import reset_repo_for_tests
from src.description_enhance import sanitize_description_html
from src.image_migrate import (
    DarazImageMigrationService,
    is_daraz_product_cdn_url,
)
from src.product_clone import build_connected_clone_draft
from src.product_create import run_supervised_create
from src.product_create_payload import (
    build_create_product_payload_preview,
    build_create_product_xml,
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


def _seed_product(repo, wid, store, item_id="100", **extra):
    product = repo.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_item_id": item_id,
            "title": extra.get("title", "Dumpling Glow Toy"),
            "title_en": extra.get("title_en", "Dumpling Glow Toy"),
            "primary_category_id": extra.get("primary_category_id", 10002730),
            "brand": extra.get("brand", "No Brand"),
            "status_raw": "Active",
            "description_en": extra.get(
                "description_en", "<p>Nice product</p>"
            ),
            "images_json": [
                {
                    "url": "https://static-01.daraz.pk/p/abc.png",
                    "position": 0,
                    "kind": "product",
                }
            ],
            "attributes_json": {
                "warranty_type": "No Warranty",
                "brand": extra.get("brand", "No Brand"),
                "short_description_en": "Short",
                "name": extra.get("title", "Dumpling Glow Toy"),
                "name_en": extra.get("title_en", "Dumpling Glow Toy"),
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
                "sale_props_json": {"color_family": "Glow"},
                "price": 999,
                "quantity": 42,
                "package_weight": 0.2,
                "package_length": 10,
                "package_width": 5,
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
        {
            "name": "short_description_en",
            "is_mandatory": 1,
            "attribute_type": "normal",
        },
        {"name": "description_en", "is_mandatory": 1, "attribute_type": "normal"},
        {"name": "SellerSku", "is_mandatory": 1, "attribute_type": "sku"},
        {"name": "price", "is_mandatory": 1, "attribute_type": "sku"},
        {"name": "package_weight", "is_mandatory": 1, "attribute_type": "sku"},
        {"name": "package_length", "is_mandatory": 1, "attribute_type": "sku"},
        {"name": "package_width", "is_mandatory": 1, "attribute_type": "sku"},
        {"name": "package_height", "is_mandatory": 1, "attribute_type": "sku"},
        {"name": "video", "is_mandatory": 0, "attribute_type": "normal"},
    ]
}


def test_is_daraz_cdn_and_external():
    assert is_daraz_product_cdn_url("https://static-01.daraz.pk/p/a.png")
    assert is_daraz_product_cdn_url("https://sg-live-02.slatic.net/p/a.jpg")
    assert not is_daraz_product_cdn_url("https://evil.example/x.png")


def test_cdn_reuse_skips_migrate():
    client = MagicMock()
    svc = DarazImageMigrationService(client, prefer_cdn_reuse=True)
    result = svc.resolve_one("https://static-01.daraz.pk/p/a.png")
    assert result.status == "completed"
    assert result.strategy == "reuse_cdn"
    assert result.migrated_url.endswith("a.png")
    client.migrate_images.assert_not_called()
    client.migrate_image.assert_not_called()


def test_external_url_requires_migration():
    client = MagicMock()
    client.migrate_image.return_value = {
        "code": "0",
        "data": {"image": {"url": "https://static-01.daraz.pk/p/migrated.png"}},
    }
    svc = DarazImageMigrationService(client, prefer_cdn_reuse=True)
    result = svc.resolve_one("https://cdn.example.com/ext.png")
    assert result.status == "completed"
    assert result.strategy == "singular_migrate"
    client.migrate_image.assert_called_once()


def test_image_poll_success_processing_failure_timeout():
    client = MagicMock()
    client.migrate_images.return_value = {"code": "0", "batch_id": "batch-ok"}
    client.migrate_image.side_effect = Exception("force batch")

    # success
    client.get_image_response.return_value = {
        "code": "0",
        "data": {"images": [{"url": "https://img.lazcdn.com/ok.png"}]},
    }
    sleeps: list[float] = []
    svc = DarazImageMigrationService(
        client,
        poll_interval_s=0,
        timeout_s=5,
        initial_wait_s=0,
        sleep_fn=lambda s: sleeps.append(s),
        prefer_cdn_reuse=False,
    )
    ok = svc.migrate_one("https://cdn.example.com/a.png")
    assert ok.status == "completed"
    assert ok.migrated_url.endswith("ok.png")
    client.get_image_response.assert_called_with("batch-ok")

    # processing then success
    client2 = MagicMock()
    client2.migrate_image.side_effect = Exception("force batch")
    client2.migrate_images.return_value = {"code": "0", "batch_id": "b2"}
    client2.get_image_response.side_effect = [
        {"code": "0", "data": {"images": []}},
        {"code": "0", "data": {"images": [{"url": "https://img.lazcdn.com/later.png"}]}},
    ]
    proc = DarazImageMigrationService(
        client2, poll_interval_s=0, timeout_s=5, initial_wait_s=0, sleep_fn=lambda s: None
    ).migrate_one("https://cdn.example.com/b.png")
    assert proc.status == "completed"

    # failure E005
    client3 = MagicMock()
    client3.migrate_image.side_effect = Exception("force batch")
    client3.migrate_images.return_value = {"code": "0", "batch_id": "bad"}
    from src.daraz_api import DarazApiError

    client3.get_image_response.side_effect = DarazApiError(
        "E005: Invalid Request Format", code="5", payload={"code": "5"}
    )
    fail = DarazImageMigrationService(
        client3, poll_interval_s=0, timeout_s=5, initial_wait_s=0, sleep_fn=lambda s: None
    ).migrate_one("https://cdn.example.com/c.png")
    assert fail.status == "failed"
    assert "5" in (fail.error or "")

    # timeout
    client4 = MagicMock()
    client4.migrate_image.side_effect = Exception("force batch")
    client4.migrate_images.return_value = {"code": "0", "batch_id": "slow"}
    client4.get_image_response.return_value = {"code": "0", "data": {"images": []}}
    timed = DarazImageMigrationService(
        client4, poll_interval_s=0, timeout_s=0, initial_wait_s=0, sleep_fn=lambda s: None
    ).migrate_one("https://cdn.example.com/d.png")
    assert timed.status == "timeout"


def test_migrate_images_uses_Images_wrapper():
    from src.daraz_api import DarazClient

    calls: list[dict] = []

    def fake_request(api_path, **kwargs):
        calls.append({"path": api_path, **kwargs})
        return {"code": "0", "batch_id": "x"}

    monkey_client = DarazClient(app_key="k", app_secret="s", access_token="t")
    monkey_client._request = fake_request  # type: ignore[method-assign]
    monkey_client.migrate_images(["https://a.example/1.png"])
    payload = calls[0]["business_params"]["payload"]
    assert "<Images>" in payload
    assert "</Images>" in payload
    # Must not use singular wrapper (causes unpollable batch_id / E005)
    assert "<Request><Image>" not in payload
    assert calls[0]["path"] == "/images/migrate"


def test_category_validation_missing_blocks():
    draft = {
        "product": {
            "title": "T",
            "title_en": "T",
            "brand": "No Brand",
            "attributes": {},
            "description_html": "",
            "short_description_html": "",
            "warranty_type": None,
        },
        "media": {"product_images": ["https://static-01.daraz.pk/p/a.png"]},
        "variants": [
            {
                "seller_sku": "MTF-X",
                "price": 1,
                "package_weight": 0.1,
                "package_length": 1,
                "package_width": 1,
                "package_height": 1,
            }
        ],
    }
    result = validate_draft_against_category(draft, SAMPLE_CATEGORY_ATTRS)
    assert result["valid"] is False
    assert any("warranty_type" in m for m in result["missing_required"])
    assert any("description_en" in m for m in result["missing_required"])


def test_category_validation_passes_complete_draft(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    dst = _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(tenancy_env, wid, src)
    result = build_connected_clone_draft(
        wid,
        source_product_id=product["id"],
        destination_store_id="store_b",
        category_attributes_payload=SAMPLE_CATEGORY_ATTRS,
        brand_query_fn=lambda **kw: {
            "module": [{"name": "No Brand", "brand_id": 1}]
        },
    )
    cat = result["draft"]["validation"]["category"]
    assert cat["valid"] is True
    assert result["draft"]["variants"][0]["seller_sku"].startswith("MTF-")


def test_brand_exact_and_unresolved():
    def exact(**kwargs):
        return {
            "module": [
                {"name": "Acme", "name_en": "Acme", "brand_id": 99},
                {"name": "Other", "brand_id": 1},
            ]
        }

    ok = resolve_brand_for_category(
        source_brand="Acme", primary_category_id=1, query_brands=exact
    )
    assert ok["status"] == "EXACT_MATCH"
    assert ok["brand_id"] == 99

    def empty(**kwargs):
        return {"module": [{"name": "Zed", "brand_id": 2}]}

    bad = resolve_brand_for_category(
        source_brand="UnknownBrandXYZ", primary_category_id=1, query_brands=empty
    )
    assert bad["status"] == "UNRESOLVED"
    assert bad["brand_id"] is None


def test_html_sanitization_strips_js_and_handlers():
    dirty = (
        '<p onmouseover="x()">Hi</p>'
        '<script>alert(1)</script>'
        '<img src="javascript:alert(1)" />'
        '<img src="https://static-01.daraz.pk/p/a.png" onerror="evil()" />'
        '<iframe src="https://x"></iframe>'
    )
    clean = sanitize_description_html(dirty)
    low = clean.lower()
    assert "script" not in low
    assert "iframe" not in low
    assert "javascript:" not in low
    assert "onmouseover" not in low
    assert "onerror" not in low
    assert "static-01.daraz.pk" in clean


def test_final_payload_preserves_mtf_and_dims(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(tenancy_env, wid, src)
    result = build_connected_clone_draft(
        wid, source_product_id=product["id"], destination_store_id="store_b"
    )
    draft = result["draft"]
    preview = build_create_product_payload_preview(draft)
    assert preview["variant_count"] == 1
    assert preview["Skus"][0]["SellerSku"].startswith("MTF-")
    assert preview["Skus"][0]["SellerSku"] != "SOURCE-SKU"
    assert preview["Skus"][0]["package_weight"] == 0.2
    assert preview["image_count"] >= 1
    xml = build_create_product_xml(draft)
    assert "PrimaryCategory" in xml
    assert "MTF-" in xml
    assert "SOURCE-SKU" not in xml


def test_create_flag_off_blocked(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(tenancy_env, wid, src)
    out = run_supervised_create(
        wid,
        source_product_id=product["id"],
        destination_store_id="store_b",
        confirm=True,
        execute=True,
    )
    assert out["status"] == "BLOCKED"


def test_api_create_probe_disabled(client, tenancy_env):
    wid = _bootstrap(client, "user-a", "a@example.com")
    store = _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(tenancy_env, wid, store)
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    res = client.post(
        f"/api/products/{product['id']}/create-probe",
        headers=headers,
        json={"destination_store_id": "store_b", "confirm": True, "execute": True},
    )
    assert res.status_code == 403


def test_cross_workspace_create_blocked(tenancy_env):
    wid_a = tenancy_env.create_workspace_with_owner("u1", "A")["workspace"]["id"]
    wid_b = tenancy_env.create_workspace_with_owner("u2", "B")["workspace"]["id"]
    store_a = _add_store(tenancy_env, wid_a, "store_a")
    _add_store(tenancy_env, wid_b, "store_b")
    product = _seed_product(tenancy_env, wid_a, store_a)
    with pytest.raises(ValueError):
        build_connected_clone_draft(
            wid_b, source_product_id=product["id"], destination_store_id="store_b"
        )


def test_duplicate_safety_blocks_supervised(tenancy_env, monkeypatch):
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "true")
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    dst = _add_store(tenancy_env, wid, "store_b")
    product = _seed_product(
        tenancy_env, wid, src, title="Same Title Exact", title_en="Same Title Exact"
    )
    _seed_product(
        tenancy_env,
        wid,
        dst,
        item_id="200",
        title="Same Title Exact",
        title_en="Same Title Exact",
    )

    from src import product_create as pc

    class FakeClient:
        def get_category_attributes(self, *_a, **_k):
            return SAMPLE_CATEGORY_ATTRS

        def query_category_brands(self, **_k):
            return {"module": [{"name": "No Brand", "brand_id": 1}]}

    monkeypatch.setattr(pc, "DarazClient", lambda **kw: FakeClient())
    monkeypatch.setattr(
        pc,
        "get_token_store",
        lambda sid: {"access_token": "tok", "store_id": sid},
    )
    out = run_supervised_create(
        wid,
        source_product_id=product["id"],
        destination_store_id="store_b",
        confirm=True,
        execute=False,
        allow_duplicates=False,
    )
    assert out["status"] == "NOT_RUN"
    assert "duplicate" in out["reason"].lower()


def test_product_detail_includes_sanitized_html(client, tenancy_env):
    wid = _bootstrap(client, "user-a", "a@example.com")
    store = _add_store(tenancy_env, wid, "store_a")
    product = _seed_product(
        tenancy_env,
        wid,
        store,
        description_en='<p>Safe</p><script>alert(1)</script>',
    )
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    res = client.get(f"/api/products/{product['id']}", headers=headers)
    assert res.status_code == 200
    safe = res.json()["product"]["description_html_safe"]
    assert "script" not in safe.lower()
    assert "Safe" in safe
