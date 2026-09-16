"""Phase 4B unit tests: SKU, package, description, clone draft, APIs, image migrate."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.auth import issue_test_token
from src.db import reset_repo_for_tests
from src.description_enhance import (
    enhance_description_with_images,
    sanitize_description_html,
)
from src.package_resolve import resolve_package_field
from src.product_clone import build_connected_clone_draft
from src.seller_sku import generate_seller_sku
from src.image_migrate import DarazImageMigrationService


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


def _seed_product_with_variant(repo, wid, store, item_id="100", **extra):
    product = repo.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_item_id": item_id,
            "title": extra.get("title", "Dumpling Glow Toy"),
            "title_en": extra.get("title_en", "Dumpling Glow Toy"),
            "primary_category_id": extra.get("primary_category_id", 10002730),
            "brand": "No Brand",
            "status_raw": "Active",
            "description_en": extra.get(
                "description_en", "<p>Nice product</p>"
            ),
            "images_json": [
                {"url": "https://static-01.daraz.pk/p/abc.png", "position": 0, "kind": "product"}
            ],
            "attributes_json": {"warranty_type": "No Warranty", "brand": "No Brand"},
            "video_ref": extra.get("video_ref"),
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
                "package_weight": extra.get("package_weight", 0.2),
                "package_length": extra.get("package_length", 10),
                "package_width": extra.get("package_width", 5),
                "package_height": extra.get("package_height", 4),
            }
        ],
    )
    return product


def test_mtf_prefix_and_collision():
    a = generate_seller_sku(title="Dumpling Bubble", sale_props={"color": "Bubble"})
    assert a.startswith("MTF-")
    assert a == a.upper() or a.startswith("MTF-")
    b = generate_seller_sku(
        title="Dumpling Bubble",
        sale_props={"color": "Bubble"},
        existing=[a],
    )
    assert b != a
    assert b.startswith("MTF-")
    assert b.endswith("-2") or "-2" in b


def test_package_connected_source_then_default_then_error():
    r = resolve_package_field(
        source_type="connected_store",
        source_value=0.5,
        workspace_default=1.0,
        field="package_weight",
    )
    assert r["source"] == "connected_source"
    assert r["value"] == 0.5

    r2 = resolve_package_field(
        source_type="connected_store",
        source_value=None,
        workspace_default=1.0,
        field="package_weight",
    )
    assert r2["source"] == "workspace_default"
    assert r2["warning"]

    r3 = resolve_package_field(
        source_type="connected_store",
        source_value=0,
        workspace_default=None,
        field="package_weight",
    )
    assert r3["source"] == "missing"
    assert r3["error"]


def test_package_public_ignores_source():
    r = resolve_package_field(
        source_type="daraz_url",
        source_value=9.9,
        workspace_default=0.5,
        field="package_weight",
    )
    assert r["source"] == "workspace_default"
    assert r["value"] == 0.5


def test_description_sanitize_and_append():
    dirty = '<p>Hi</p><script>alert(1)</script><img src="https://static-01.daraz.pk/p/a.png" />'
    clean = sanitize_description_html(dirty)
    assert "script" not in clean.lower()
    assert "static-01.daraz.pk" in clean

    enhanced = enhance_description_with_images(
        "<p>No images here</p>",
        ["https://static-01.daraz.pk/p/b.png", "https://static-01.daraz.pk/p/c.png"],
    )
    assert enhanced["enhancement"] == "appended"
    assert "b.png" in enhanced["html"]

    preserved = enhance_description_with_images(
        '<p><img src="https://static-01.daraz.pk/p/a.png" /></p>',
        ["https://static-01.daraz.pk/p/a.png", "https://static-01.daraz.pk/p/b.png"],
    )
    assert preserved["enhancement"] == "preserved"


def test_image_migrate_states():
    client = MagicMock()
    client.migrate_images.return_value = {"code": "0", "batch_id": "batch-1"}

    def _resp(batch_id):
        return {
            "code": "0",
            "data": {"images": [{"url": "https://img.lazcdn.com/migrated.png"}]},
        }

    client.get_image_response.side_effect = _resp
    sleeps: list[float] = []
    svc = DarazImageMigrationService(
        client, poll_interval_s=0, timeout_s=5, sleep_fn=lambda s: sleeps.append(s)
    )
    result = svc.migrate_one("https://static-01.daraz.pk/p/x.png")
    assert result.status == "completed"
    assert result.migrated_url.endswith("migrated.png")

    client2 = MagicMock()
    client2.migrate_images.side_effect = Exception("boom")
    failed = DarazImageMigrationService(client2, timeout_s=1).migrate_one("http://x")
    assert failed.status == "failed"


def test_clone_draft_generates_mtf_and_preserves_dims(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    dst = _add_store(tenancy_env, wid, "store_b")
    product = _seed_product_with_variant(tenancy_env, wid, src, video_ref="vid-1")
    result = build_connected_clone_draft(
        wid, source_product_id=product["id"], destination_store_id=dst["store_id"]
    )
    draft = result["draft"]
    assert draft["variants"][0]["seller_sku"].startswith("MTF-")
    assert draft["variants"][0]["seller_sku"] != "SOURCE-SKU"
    assert draft["variants"][0]["package_weight"] == 0.2
    assert draft["variants"][0]["quantity"] == 1  # default initial qty
    assert "Video" in draft["validation"]["fidelity"]["not_available"]
    assert result["warnings"]


def test_clone_draft_blocks_same_store(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    product = _seed_product_with_variant(tenancy_env, wid, src)
    with pytest.raises(ValueError, match="differ"):
        build_connected_clone_draft(
            wid, source_product_id=product["id"], destination_store_id="store_a"
        )


def test_clone_draft_cross_workspace_blocked(tenancy_env):
    wid_a = tenancy_env.create_workspace_with_owner("u1", "A")["workspace"]["id"]
    wid_b = tenancy_env.create_workspace_with_owner("u2", "B")["workspace"]["id"]
    store_a = _add_store(tenancy_env, wid_a, "store_a")
    _add_store(tenancy_env, wid_b, "store_b")
    product = _seed_product_with_variant(tenancy_env, wid_a, store_a)
    with pytest.raises(ValueError):
        build_connected_clone_draft(
            wid_b, source_product_id=product["id"], destination_store_id="store_b"
        )


def test_api_products_list_and_create_probe_disabled(client, tenancy_env):
    wid = _bootstrap(client, "user-a", "a@example.com")
    store = _add_store(tenancy_env, wid, "store_a")
    _add_store(tenancy_env, wid, "store_b")
    product = _seed_product_with_variant(tenancy_env, wid, store)
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid

    res = client.get("/api/products", headers=headers)
    assert res.status_code == 200
    assert res.json()["total"] >= 1

    detail = client.get(f"/api/products/{product['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["product"]["id"] == product["id"]

    draft = client.post(
        f"/api/products/{product['id']}/clone-draft",
        headers=headers,
        json={"destination_store_id": "store_b"},
    )
    assert draft.status_code == 200
    assert draft.json()["draft"]["variants"][0]["seller_sku"].startswith("MTF-")

    probe = client.post(
        f"/api/products/{product['id']}/create-probe",
        headers=headers,
        json={"destination_store_id": "store_b", "confirm": True},
    )
    assert probe.status_code == 403


def test_defaults_api(client, tenancy_env):
    wid = _bootstrap(client, "user-a", "a@example.com")
    headers = _auth("user-a", "a@example.com")
    headers["X-Workspace-Id"] = wid
    put = client.put(
        "/api/product-defaults",
        headers=headers,
        json={
            "default_package_weight": 0.5,
            "default_package_length": 20,
            "default_package_width": 15,
            "default_package_height": 10,
            "default_initial_quantity": 5,
        },
    )
    assert put.status_code == 200
    assert put.json()["defaults"]["default_package_weight"] == 0.5
    got = client.get("/api/product-defaults", headers=headers)
    assert got.json()["defaults"]["default_initial_quantity"] == 5


def test_missing_package_uses_defaults_in_draft(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    src = _add_store(tenancy_env, wid, "store_a")
    dst = _add_store(tenancy_env, wid, "store_b")
    tenancy_env.upsert_product_defaults(
        wid,
        {
            "default_package_weight": 0.5,
            "default_package_length": 20,
            "default_package_width": 15,
            "default_package_height": 10,
        },
    )
    product = _seed_product_with_variant(
        tenancy_env,
        wid,
        src,
        package_weight=None,
        package_length=None,
        package_width=None,
        package_height=None,
    )
    # clear dims on variant
    variants = tenancy_env.list_daraz_product_variants(wid, product["id"])
    tenancy_env.replace_product_variants(
        wid,
        product["id"],
        [
            {
                **variants[0],
                "package_weight": None,
                "package_length": None,
                "package_width": None,
                "package_height": None,
            }
        ],
    )
    result = build_connected_clone_draft(
        wid, source_product_id=product["id"], destination_store_id="store_b"
    )
    v0 = result["draft"]["variants"][0]
    assert v0["package_weight"] == 0.5
    assert any("workspace default" in w.lower() for w in result["warnings"])
