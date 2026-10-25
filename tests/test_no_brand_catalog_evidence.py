"""Local catalog brand evidence for No Brand diagnostic — fakes only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from src.brand_diagnostics import (
    build_catalog_brand_evidence,
    diagnose_category_no_brand,
    public_lookup_fields,
)
from src.brand_resolve import clear_no_brand_cache_for_tests
from src.db import reset_repo_for_tests


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
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_no_brand_cache_for_tests()
    return reset_repo_for_tests()


def _add_store(repo, wid: str, store_id: str) -> dict[str, Any]:
    return repo.upsert_store(
        wid,
        {
            "store_id": store_id,
            "account": f"{store_id}@x.com",
            "seller_id": f"s-{store_id}",
            "display_name": store_id,
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 86400,
            "country": "pk",
        },
    )


def _seed_product(
    repo,
    wid: str,
    store: dict[str, Any],
    *,
    item_id: str,
    brand: str | None,
    category_id: int,
) -> dict[str, Any]:
    return repo.upsert_daraz_product(
        {
            "workspace_id": wid,
            "store_id": store["id"],
            "daraz_item_id": item_id,
            "title": f"Product {item_id}",
            "primary_category_id": category_id,
            "brand": brand,
            "status_raw": "Active",
        }
    )


class GuardClient:
    def __init__(self) -> None:
        self.mutating: list[str] = []
        self.brand_calls = 0

    def query_category_brands(self, **_k):
        self.brand_calls += 1
        return {
            "data": {
                "module": [{"name": "Acme", "brand_id": 1}],
                "total_record": 1,
            }
        }

    def create_product(self, *_a, **_k):
        self.mutating.append("create_product")
        raise AssertionError("must not create")

    def update_price(self, *_a, **_k):
        self.mutating.append("update_price")
        raise AssertionError("must not update price")

    def migrate_image(self, *_a, **_k):
        self.mutating.append("migrate_image")
        raise AssertionError("must not migrate")


def test_public_lookup_fields_exposes_ordering_and_matched_row():
    out = public_lookup_fields(
        {
            "strategy": "binary_scan",
            "pages": 12,
            "stop_reason": "page_limit",
            "ordering": "unreliable_in_window",
            "matched_start_row": 400,
            "total_record": 78000,
            "name_filter_effective": False,
            "secret_token": "should-not-copy",
        },
        cache_hit=False,
    )
    assert out["pages"] == 12
    assert out["stop_reason"] == "page_limit"
    assert out["ordering"] == "unreliable_in_window"
    assert out["matched_start_row"] == 400
    assert "secret_token" not in out


def test_catalog_evidence_not_synced(tenancy_env):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    store = _add_store(tenancy_env, wid, "store_a")
    evidence = build_catalog_brand_evidence(
        wid,
        store_uuid=store["id"],
        primary_category_id="10002730",
        repo=tenancy_env,
    )
    assert evidence["status"] == "NOT_SYNCED"
    assert evidence["workspace"]["product_count"] == 0


def test_catalog_evidence_with_matching_brands_and_ignores_other_categories(
    tenancy_env,
):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    dest = _add_store(tenancy_env, wid, "dest")
    other = _add_store(tenancy_env, wid, "other")
    _seed_product(
        tenancy_env, wid, dest, item_id="1", brand="Acme", category_id=10002730
    )
    _seed_product(
        tenancy_env, wid, dest, item_id="2", brand="Acme", category_id=10002730
    )
    _seed_product(
        tenancy_env, wid, dest, item_id="3", brand="No Brand", category_id=10002730
    )
    _seed_product(
        tenancy_env, wid, dest, item_id="4", brand="No Brand", category_id=999
    )
    _seed_product(
        tenancy_env, wid, other, item_id="5", brand="OtherCo", category_id=10002730
    )

    evidence = build_catalog_brand_evidence(
        wid,
        store_uuid=dest["id"],
        primary_category_id="10002730",
        repo=tenancy_env,
    )
    assert evidence["status"] == "OK"
    assert evidence["workspace"]["category_product_count"] == 4  # not item 4
    dest_top = {
        row["brand"]: row["count"]
        for row in evidence["destination_store"]["top_brands_in_category"]
    }
    assert dest_top.get("Acme") == 2
    assert dest_top.get("No Brand") == 1
    assert "OtherCo" not in dest_top  # other store ignored for destination scope
    assert evidence["destination_store"]["no_brand_sample_item_ids"] == ["3"]
    assert "4" not in evidence["workspace"]["no_brand_sample_item_ids"]
    assert "3" in evidence["workspace"]["no_brand_sample_item_ids"]


def test_diagnose_includes_lookup_and_catalog_evidence_without_mutations(
    tenancy_env, monkeypatch
):
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    dest = _add_store(tenancy_env, wid, "dest")
    _seed_product(
        tenancy_env, wid, dest, item_id="10", brand="No Brand", category_id=10002730
    )
    client = GuardClient()
    monkeypatch.setattr("src.ops.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr("src.token_refresh.refresh_store_tokens", lambda **_k: [])

    out = diagnose_category_no_brand(
        wid, store_ref="dest", primary_category_id="10002730"
    )
    assert out["status"] in {"UNVERIFIED", "UNRESOLVED", "NO_BRAND"}
    assert "lookup" in out
    assert "pages" in out["lookup"]
    assert out["catalog_evidence"]["status"] == "OK"
    assert out["catalog_evidence"]["destination_store"]["no_brand_sample_item_ids"] == [
        "10"
    ]
    # Catalog evidence must never unlock create: Acme-only API still not NO_BRAND
    # unless brands/query returns a No Brand row.
    assert out["status"] != "NO_BRAND" or out.get("brand")
    assert client.mutating == []
    assert client.brand_calls >= 1


def test_catalog_evidence_does_not_change_unverified_status(tenancy_env, monkeypatch):
    """Even with local No Brand listings, status stays non-NO_BRAND without API row."""
    wid = tenancy_env.create_workspace_with_owner("u1", "W")["workspace"]["id"]
    dest = _add_store(tenancy_env, wid, "dest")
    for i in range(3):
        _seed_product(
            tenancy_env,
            wid,
            dest,
            item_id=str(100 + i),
            brand="No Brand",
            category_id=10002730,
        )
    client = GuardClient()
    monkeypatch.setattr("src.ops.client_for_store", lambda *_a, **_k: client)
    monkeypatch.setattr("src.token_refresh.refresh_store_tokens", lambda **_k: [])

    out = diagnose_category_no_brand(
        wid, store_ref="dest", primary_category_id="10002730"
    )
    assert out["status"] != "NO_BRAND"
    assert out["no_brand_found"] is False
    assert out["catalog_evidence"]["status"] == "OK"
    assert len(out["catalog_evidence"]["destination_store"]["no_brand_sample_item_ids"]) == 3
