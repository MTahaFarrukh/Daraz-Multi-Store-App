"""Deep scan, persist, brand_id payload, item-limit & image preflight — fakes only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from src.brand_diagnostics import diagnose_category_no_brand
from src.brand_resolve import (
    _MAX_LOOKUP_CALLS,
    clear_no_brand_cache_for_tests,
    deep_scan_no_brand,
    resolve_destination_no_brand,
)
from src.daraz_api import DarazApiError
from src.daraz_error_classify import classify_daraz_create_error
from src.db import reset_repo_for_tests
from src.image_preflight import preflight_images_for_dry_run
from src.product_create_payload import build_create_product_xml
from src.product_create_reconcile import reconcile_product_create_attempt
from src.seller_item_limit import interpret_seller_item_limit, preflight_seller_item_limit


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
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_no_brand_cache_for_tests()
    return reset_repo_for_tests()


def _add_store(repo, wid: str, store_id: str = "mtf") -> dict[str, Any]:
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


class SyntheticBrandCatalog:
    """78k-scale catalog: non-alphabetical, name filter ignored."""

    def __init__(
        self,
        *,
        total: int = 78630,
        page_size: int = 200,
        no_brand_at: int | None = 50000,
        no_brand_id: int = 424242,
        ambiguous_extra_id: int | None = None,
        rate_limit_after_pages: int | None = None,
    ) -> None:
        self.total = total
        self.page_size = page_size
        self.no_brand_at = no_brand_at
        self.no_brand_id = no_brand_id
        self.ambiguous_extra_id = ambiguous_extra_id
        self.rate_limit_after_pages = rate_limit_after_pages
        self.calls: list[dict[str, Any]] = []
        self._pages = 0
        self.mutating: list[str] = []

    def _row(self, idx: int) -> dict[str, Any]:
        if self.no_brand_at is not None and idx == self.no_brand_at:
            row = {
                "name": "No Brand",
                "name_en": "No Brand",
                "brand_id": self.no_brand_id,
                "global_identifier": "No Brand",
            }
            return row
        if (
            self.ambiguous_extra_id is not None
            and self.no_brand_at is not None
            and idx == self.no_brand_at + 7
        ):
            return {
                "name": "no-brand",
                "name_en": "NoBrand",
                "brand_id": self.ambiguous_extra_id,
                "global_identifier": "nobrand",
            }
        # Non-alphabetical: reverse-ish labels so binary/alpha strategies fail
        label = f"Zeta-{self.total - idx:05d}"
        return {
            "name": label,
            "name_en": label,
            "brand_id": 1000 + idx,
            "global_identifier": label,
        }

    def query_category_brands(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        # Name filter intentionally ignored (real Daraz PK behaviour)
        start = int(kwargs.get("start_row") or 0)
        size = int(kwargs.get("page_size") or self.page_size)
        self._pages += 1
        if (
            self.rate_limit_after_pages is not None
            and self._pages > self.rate_limit_after_pages
        ):
            raise DarazApiError("ApiCallLimit", code="ApiCallLimit", http_status=429)
        rows = [
            self._row(i)
            for i in range(start, min(start + size, self.total))
        ]
        return {
            "code": "0",
            "data": {
                "module": rows,
                "total_record": self.total,
                "start_row": start,
                "page_size": size,
            },
        }


def test_create_path_budget_unchanged():
    assert _MAX_LOOKUP_CALLS == 48


def test_deep_scan_finds_no_brand_in_synthetic_78k():
    cat = SyntheticBrandCatalog(no_brand_at=12345, no_brand_id=99)
    # Resume across requests (~60 pages each)
    first = deep_scan_no_brand(
        primary_category_id="10000719",
        query_brands=cat.query_category_brands,
        marketplace="pk",
        scan_from_start_row=0,
        max_pages=60,
        delay_s=0,
        page_size=200,
    )
    assert first["status"] == "UNVERIFIED"
    assert first["lookup"]["next_start_row"] == 60 * 200
    second = deep_scan_no_brand(
        primary_category_id="10000719",
        query_brands=cat.query_category_brands,
        marketplace="pk",
        scan_from_start_row=first["lookup"]["next_start_row"],
        max_pages=60,
        delay_s=0,
        page_size=200,
        prior_candidates=first.get("candidates"),
    )
    assert second["status"] == "NO_BRAND"
    assert second["brand_id"] == 99
    assert second["brand"] == "No Brand"


def test_deep_scan_rate_limited_stops_without_retry():
    cat = SyntheticBrandCatalog(
        no_brand_at=None, rate_limit_after_pages=2, total=5000
    )
    out = deep_scan_no_brand(
        primary_category_id="10000719",
        query_brands=cat.query_category_brands,
        scan_from_start_row=0,
        max_pages=60,
        delay_s=0,
        page_size=200,
    )
    assert out["status"] == "UNVERIFIED"
    assert out["reason"] == "rate_limited" or out["lookup"]["stop_reason"] == "rate_limited"
    assert cat._pages == 3  # 2 ok + 1 raising


def test_deep_scan_ambiguous_candidates():
    cat = SyntheticBrandCatalog(
        no_brand_at=10, no_brand_id=1, ambiguous_extra_id=2, total=400
    )
    out = deep_scan_no_brand(
        primary_category_id="10000719",
        query_brands=cat.query_category_brands,
        scan_from_start_row=0,
        max_pages=5,
        delay_s=0,
        page_size=200,
    )
    assert out["status"] == "UNRESOLVED"
    assert out["reason"] == "ambiguous_no_brand"
    assert len(out["candidates"]) == 2


def test_deep_scan_complete_not_found():
    cat = SyntheticBrandCatalog(no_brand_at=None, total=500)
    out = deep_scan_no_brand(
        primary_category_id="10000719",
        query_brands=cat.query_category_brands,
        scan_from_start_row=0,
        max_pages=60,
        delay_s=0,
        page_size=200,
    )
    assert out["status"] == "UNRESOLVED"
    assert out["reason"] == "not_found_complete_scan"
    assert out["lookup"]["complete"] is True


def test_persisted_short_circuits_resolver(tenancy_env):
    repo = tenancy_env
    owner = repo.create_workspace_with_owner("u1", "WS")
    wid = owner["workspace"]["id"]
    _add_store(repo, wid)
    repo.upsert_verified_category_no_brand(
        {
            "marketplace": "pk",
            "primary_category_id": "10000719",
            "brand_name": "No Brand",
            "brand_id": "777",
            "evidence": {"matched_start_row": 12345, "total_record": 78630},
        }
    )
    clear_no_brand_cache_for_tests()
    calls = {"n": 0}

    def boom(**kwargs):
        calls["n"] += 1
        raise AssertionError("should not scan when persisted")

    out = resolve_destination_no_brand(
        primary_category_id="10000719",
        query_brands=boom,
        marketplace="pk",
    )
    assert out["status"] == "NO_BRAND"
    assert out["brand_id"] == "777"
    assert out.get("persisted") is True
    assert calls["n"] == 0


def test_payload_carries_brand_id():
    draft = {
        "product": {
            "title": "T",
            "primary_category_id": "10000719",
            "brand": "No Brand",
            "brand_id": 424242,
            "attributes": {},
        },
        "media": {"resolved_images": ["https://static-01.daraz.pk/p/x.jpg"]},
        "variants": [
            {
                "seller_sku": "MTF-1",
                "price": 100,
                "quantity": 1,
                "package_weight": "0.1",
                "package_length": "1",
                "package_width": "1",
                "package_height": "1",
            }
        ],
        "brand_resolution": {"status": "NO_BRAND", "brand_id": 424242},
    }
    xml = build_create_product_xml(draft)
    assert "<brand_id>424242</brand_id>" in xml
    assert "<brand>No Brand</brand>" in xml


def test_item_limit_blocked_and_failed():
    ok = interpret_seller_item_limit(
        {"data": {"onlineItemCount": 100, "itemLimit": 100}}
    )
    assert ok["limit_reached"] is True
    blocked = preflight_seller_item_limit(
        lambda: {"data": {"onlineItemCount": 50, "itemLimit": 40}}
    )
    assert blocked["blocks_create"] is True
    failed = preflight_seller_item_limit(
        lambda: (_ for _ in ()).throw(DarazApiError("boom", code="E001"))
    )
    assert failed["ok"] is False
    assert failed["blocks_create"] is True


def test_image_preflight_cases():
    def fetch(url: str) -> dict[str, Any]:
        if "big" in url:
            return {"ok": True, "content_type": "image/jpeg", "content_length": 2_000_000}
        if "gif" in url:
            return {"ok": True, "content_type": "image/gif", "content_length": 100}
        if "fail" in url:
            return {"ok": False, "content_type": None, "content_length": None, "error": "boom"}
        return {"ok": True, "content_type": "image/png", "content_length": 500}

    out = preflight_images_for_dry_run(
        [
            "https://example.com/a.jpg",
            "https://example.com/big.jpg",
            "https://example.com/x.gif",
            "https://example.com/fail.png",
            "https://static-01.daraz.pk/p/ok.jpg",
        ],
        fetch=fetch,
    )
    assert out["mutating_calls"] == 0
    assert out["cdn_urls"] == 1
    issues = {i["issue"] for i in out["issues"]}
    assert "size" in issues
    assert "format" in issues
    assert "fetch_failed" in issues

    too_many = preflight_images_for_dry_run(
        [f"https://example.com/{i}.jpg" for i in range(9)],
        fetch=lambda u: {
            "ok": True,
            "content_type": "image/jpeg",
            "content_length": 10,
        },
    )
    assert any(i["issue"] == "migrate_batch_count" for i in too_many["issues"])


def test_error_code_classification():
    brand = classify_daraz_create_error(
        code="Invalid",
        message="Brand:C011 invalid brand",
        payload={"detail": [{"message": "Brand:C011"}]},
    )
    assert brand["class"] == "DEFINITE_BRAND_REJECTION"
    assert brand["blocks_corroboration"] is True
    dup = classify_daraz_create_error(
        message="Attribute C008 duplicate",
        payload={"detail": "C008"},
    )
    assert dup["class"] == "DEFINITE_DUPLICATE_ATTRIBUTE"
    sku = classify_daraz_create_error(message="Duplicate SellerSku already exists")
    assert sku["class"] == "DEFINITE_DUPLICATE_SELLER_SKU"
    unk = classify_daraz_create_error(message="weird", http_status=503)
    assert unk["definite"] is False


def test_reconcile_never_retries_create(tenancy_env, monkeypatch):
    repo = tenancy_env
    owner = repo.create_workspace_with_owner("u1", "WS")
    wid = owner["workspace"]["id"]
    store = _add_store(repo, wid)
    attempt = repo.create_product_attempt(
        {
            "workspace_id": wid,
            "source_type": "public_url",
            "source_identity": "https://x",
            "destination_store_id": store["id"],
            "request_fingerprint": "fp1",
            "state": "NEEDS_RECONCILIATION",
            "generated_seller_skus": [{"seller_sku": "MTF-A"}],
            "verification_state": "UNVERIFIED",
        }
    )

    class Client:
        def __init__(self):
            self.create_calls = 0
            self.item_calls = 0

        def create_product(self, *a, **k):
            self.create_calls += 1
            raise AssertionError("must not auto retry create")

        def get_product_item(self, item_id=None, seller_sku=None):
            self.item_calls += 1
            raise DarazApiError("not found", code="404", http_status=404)

        def get_products(self, **kwargs):
            return {"code": "0", "data": {"products": []}}

        def get_qc_status(self, **kwargs):
            return {"code": "0", "data": []}

    client = Client()
    monkeypatch.setattr(
        "src.product_create_reconcile._client_for_destination",
        lambda *a, **k: client,
    )
    out = reconcile_product_create_attempt(wid, attempt["id"])
    assert client.create_calls == 0
    assert out.get("auto_retry_create") in (None, False)
    assert out["status"] in {"FAILED_SAFE_TO_RETRY", "NEEDS_RECONCILIATION"}


def test_diagnose_deep_persists_and_blocks_without_no_brand(tenancy_env, monkeypatch):
    repo = tenancy_env
    owner = repo.create_workspace_with_owner("u1", "WS")
    wid = owner["workspace"]["id"]
    store = _add_store(repo, wid)
    cat = SyntheticBrandCatalog(no_brand_at=5, no_brand_id=55, total=50)

    class Client:
        def query_category_brands(self, **kwargs):
            return cat.query_category_brands(**kwargs)

        def create_product(self, *a, **k):
            raise AssertionError("no create")

        def migrate_image(self, *a, **k):
            raise AssertionError("no migrate")

        def migrate_images(self, *a, **k):
            raise AssertionError("no migrate")

    monkeypatch.setattr("src.ops.client_for_store", lambda s: Client())
    monkeypatch.setattr(
        "src.token_refresh.refresh_store_tokens", lambda **kwargs: []
    )
    out = diagnose_category_no_brand(
        wid,
        store_ref=store["store_id"],
        primary_category_id="10000719",
        deep=True,
        scan_from_start_row=0,
    )
    assert out["status"] == "NO_BRAND"
    assert out["brand_id"] == 55
    assert out.get("persisted") is True
    persisted = repo.get_verified_category_no_brand("pk", "10000719")
    assert persisted and str(persisted["brand_id"]) == "55"

    # Rejection block prevents trusting scan
    repo.upsert_category_brand_rejection_block(
        {
            "store_id": store["store_id"],
            "primary_category_id": "10000719",
            "detail_code": "Brand:C011",
            "message": "blocked",
        }
    )
    blocked = diagnose_category_no_brand(
        wid,
        store_ref=store["store_id"],
        primary_category_id="10000719",
        deep=True,
    )
    assert blocked["status"] == "UNVERIFIED"
    assert blocked["reason"] == "brand_rejection_block"
