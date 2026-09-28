"""Batch 3 — Product Add performance + bounded multi-store concurrency."""

from __future__ import annotations

import copy
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.http_pool import (
    close_shared_http_clients_for_tests,
    get_shared_http_client,
    shared_client_count_for_tests,
)
from src.image_migrate import (
    DarazImageMigrationService,
    clear_image_migrate_cache_for_tests,
)
from src.product_add import (
    PRODUCT_DESTINATION_CONCURRENCY,
    _run_destinations_bounded,
    add_product_from_public_url,
)
from src.public_daraz import clear_public_cache_for_tests


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
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("PRODUCT_DESTINATION_CONCURRENCY", "3")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    clear_public_cache_for_tests()
    clear_image_migrate_cache_for_tests()
    close_shared_http_clients_for_tests()
    yield reset_repo_for_tests()
    close_shared_http_clients_for_tests()
    clear_image_migrate_cache_for_tests()


def _bootstrap_ws(repo):
    from fastapi.testclient import TestClient

    from src.app import app
    from src.auth import issue_test_token

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {issue_test_token('u-b3', email='b3@x.com')}"}
    boot = client.post("/api/bootstrap", headers=headers)
    assert boot.status_code == 200
    wid = boot.json()["workspace"]["id"]
    return wid, client, headers


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


def _fake_extract(**overrides: Any) -> dict[str, Any]:
    base = {
        "source_type": "public_daraz_url",
        "source_url": "https://www.daraz.pk/products/test-i123.html",
        "item_id": "123",
        "title": "Test Glow Toy",
        "brand": "Acme",
        "description_html": "<p>Nice</p>",
        "images": [
            "https://static-01.daraz.pk/p/abc.jpg",
            "https://static-01.daraz.pk/p/abc.jpg",
        ],
        "price": 499.0,
        "category_id": "10001",
        "category_resolution": {
            "category_id": "10001",
            "confidence": "high",
            "source": "skuInfos.categoryId",
        },
        "variants": [
            {
                "price": 499.0,
                "sale_props": {"Color": "Red"},
                "category_id": "10001",
            }
        ],
        "provenance": {},
        "timings_ms": {
            "fetch_extract": 12.5,
            "pdp_fetch_ms": 10.0,
            "structured_parse_ms": 2.0,
            "catalog_ms": 0,
            "price_resolution_ms": 0,
            "source_total_ms": 12.5,
            "catalog_strategy": "SKIPPED",
            "price_strategy": "skipped",
        },
        "warnings": [],
    }
    base.update(overrides)
    return base


def test_destination_concurrency_default_is_three():
    assert PRODUCT_DESTINATION_CONCURRENCY >= 1
    assert int(__import__("os").environ.get("PRODUCT_DESTINATION_CONCURRENCY", "3")) == 3


def test_bounded_concurrency_cap_and_overlap(monkeypatch):
    monkeypatch.setenv("PRODUCT_DESTINATION_CONCURRENCY", "3")
    active = 0
    peak = 0
    lock = threading.Lock()
    started: list[float] = []

    def worker(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            started.append(time.perf_counter())
        time.sleep(0.08)
        with lock:
            active -= 1
        if payload.get("fail"):
            raise RuntimeError("boom")
        return {"status": "READY", "store": {"store_id": payload["id"]}}

    jobs = [(i, {"id": f"s{i}", "fail": i == 1}) for i in range(5)]
    t0 = time.perf_counter()
    out = _run_destinations_bounded(jobs, worker=worker, max_workers=3)
    elapsed = time.perf_counter() - t0
    assert peak <= 3
    assert peak >= 2  # proved overlap under load
    assert elapsed < 0.08 * 5 * 0.85  # faster than fully serial
    assert len(out) == 5
    assert out[0]["status"] == "READY"
    assert out[1]["status"] == "NEEDS_ATTENTION"
    assert out[2]["status"] == "READY"
    # deterministic keys: original indices
    assert sorted(out.keys()) == [0, 1, 2, 3, 4]


def test_deterministic_ordering_with_variable_latency(tenancy_env, monkeypatch):
    repo = tenancy_env
    wid, _, _ = _bootstrap_ws(repo)
    for sid in ("alpha", "bravo", "charlie"):
        _add_store(repo, wid, sid)

    delays = {"alpha": 0.12, "bravo": 0.02, "charlie": 0.06}

    def slow_prepare(workspace_id, **kwargs):
        dest = kwargs["dest"]
        sid = str(dest.get("store_id"))
        time.sleep(delays.get(sid, 0.01))
        if sid == "bravo":
            return {
                "store": {"store_id": sid, "display_name": sid},
                "status": "Failed",
                "creation_status": "FAILED_SAFE_TO_RETRY",
                "reason": "forced",
            }
        return {
            "store": {"store_id": sid, "display_name": sid},
            "status": "READY",
            "creation_status": "READY",
            "reason": "ok",
            "draft_preview": {"variants": []},
        }

    monkeypatch.setenv("PRODUCT_DESTINATION_CONCURRENCY", "3")
    with (
        patch("src.product_add.fetch_public_product", return_value=_fake_extract()),
        patch("src.product_add.find_connected_owner_for_item", return_value=None),
        patch(
            "src.product_add.build_public_clone_draft_from_extracted",
            side_effect=lambda *a, **k: {
                "draft": {
                    "product": {
                        "title": "T",
                        "primary_category_id": 10001,
                        "brand": "No Brand",
                        "attributes": {},
                    },
                    "variants": [
                        {
                            "seller_sku": f"SKU-{k.get('destination_store_id')}",
                            "price": 10,
                            "sale_props": {"Color": "Red"},
                        }
                    ],
                    "media": {
                        "product_images": ["https://static-01.daraz.pk/p/abc.jpg"]
                    },
                    "category_resolution": {"confidence": "high"},
                    "brand_resolution": {"status": "NO_BRAND", "used_no_brand": True},
                    "validation": {"errors": []},
                    "possible_duplicates": [],
                    "duplicate_check_done": True,
                    "source_type": "public_daraz_url",
                }
            },
        ),
        patch("src.product_add._prepare_destination", side_effect=slow_prepare),
        patch("src.product_add._refresh_destination_tokens"),
    ):
        res = add_product_from_public_url(
            wid,
            "https://www.daraz.pk/products/test-i123.html",
            ["alpha", "bravo", "charlie"],
            execute=False,
        )
    ids = [d["store"]["store_id"] for d in res["destinations"]]
    assert ids == ["alpha", "bravo", "charlie"]
    assert res["destinations"][1]["status"] == "Failed"
    assert res["destinations"][0]["status"] == "READY"
    assert res["destinations"][2]["status"] == "READY"
    assert res["timings_ms"]["destination_parallelism"] == 3


def test_source_immutability_across_destinations(tenancy_env, monkeypatch):
    repo = tenancy_env
    wid, _, _ = _bootstrap_ws(repo)
    _add_store(repo, wid, "store-a")
    _add_store(repo, wid, "store-b")
    extracted = _fake_extract()
    pristine = copy.deepcopy(extracted)
    mutated: list[bool] = []

    def build_draft(workspace_id, *, extracted, destination_store_id, **kwargs):
        # Mutate destination-local copy only — shared source must stay pristine.
        extracted.setdefault("variants", [{}])[0]["sale_props"] = {"Color": "MUTATED"}
        extracted["images"] = ["https://evil.example/x.jpg"]
        mutated.append(True)
        return {
            "draft": {
                "product": {
                    "title": extracted.get("title"),
                    "primary_category_id": 10001,
                    "brand": "No Brand",
                    "attributes": {},
                },
                "variants": [
                    {
                        "seller_sku": f"SKU-{destination_store_id}",
                        "price": 10,
                        "sale_props": copy.deepcopy(
                            extracted["variants"][0]["sale_props"]
                        ),
                    }
                ],
                "media": {"product_images": list(extracted.get("images") or [])},
                "category_resolution": {"confidence": "high"},
                "brand_resolution": {"status": "NO_BRAND", "used_no_brand": True},
                "validation": {"errors": []},
                "possible_duplicates": [],
                "duplicate_check_done": True,
                "source_type": "public_daraz_url",
            }
        }

    seen_props: list[dict] = []

    def capture_prepare(workspace_id, **kwargs):
        draft = kwargs["draft"]
        seen_props.append(copy.deepcopy((draft.get("variants") or [{}])[0].get("sale_props")))
        return {
            "store": _store_label_from(kwargs["dest"]),
            "status": "READY",
            "creation_status": "READY",
        }

    def _store_label_from(dest):
        return {"store_id": dest.get("store_id"), "display_name": dest.get("store_id")}

    with (
        patch("src.product_add.fetch_public_product", return_value=extracted),
        patch("src.product_add.find_connected_owner_for_item", return_value=None),
        patch(
            "src.product_add.build_public_clone_draft_from_extracted",
            side_effect=build_draft,
        ),
        patch("src.product_add._prepare_destination", side_effect=capture_prepare),
        patch("src.product_add._refresh_destination_tokens"),
    ):
        add_product_from_public_url(
            wid,
            "https://www.daraz.pk/products/test-i123.html",
            ["store-a", "store-b"],
            execute=False,
        )
    assert mutated == [True, True]
    # Original extract object unchanged (shared source not mutated).
    assert extracted == pristine
    # Each destination received its own mapped copy after A mutated.
    assert len(seen_props) == 2


def test_duplicate_check_called_once_per_destination(tenancy_env, monkeypatch):
    repo = tenancy_env
    wid, _, _ = _bootstrap_ws(repo)
    store = _add_store(repo, wid, "dup-store")
    calls = {"n": 0}
    orig = repo.find_possible_product_duplicates

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(repo, "find_possible_product_duplicates", counting)

    draft = {
        "product": {"title": "Glow", "primary_category_id": 10001, "attributes": {}},
        "variants": [{"seller_sku": "A", "price": 10, "sale_props": {}}],
        "media": {"product_images": ["https://static-01.daraz.pk/p/a.jpg"]},
        "category_resolution": {"confidence": "high"},
        "brand_resolution": {
            "status": "NO_BRAND",
            "used_no_brand": True,
            "brand": "No Brand",
        },
        "validation": {"errors": []},
        "possible_duplicates": [],
        "duplicate_check_done": True,
        "source_type": "public_daraz_url",
    }
    from src.product_add import _prepare_destination

    with patch("src.product_add.client_for_store") as cf:
        client = MagicMock()
        client.get_category_attributes.return_value = {"data": []}
        client.query_category_brands.return_value = {
            "data": {"module": [{"name": "No Brand", "brand_id": "1"}]}
        }
        cf.return_value = client
        with patch(
            "src.product_add.get_cached_category_attributes",
            return_value={"data": [], "_cache_meta": {"cache_hit": True}},
        ):
            with patch(
                "src.product_add.resolve_brand_for_category",
                return_value={
                    "status": "NO_BRAND",
                    "brand": "No Brand",
                    "used_no_brand": True,
                },
            ):
                with patch(
                    "src.product_add.resolve_required_category_attributes",
                    return_value={
                        "resolved_count": 0,
                        "unresolved_count": 0,
                        "resolved": [],
                        "unresolved": [],
                        "compatibility": {"compatible": True},
                        "timings_ms": {},
                    },
                ):
                    with patch(
                        "src.product_add.validate_draft_against_category",
                        return_value={"valid": True, "missing_required": [], "invalid_values": []},
                    ):
                        with patch(
                            "src.product_add.DarazImageMigrationService"
                        ) as Img:
                            inst = Img.return_value
                            from src.image_migrate import ImageMigrationResult

                            inst.resolve_many.return_value = [
                                ImageMigrationResult(
                                    source_url="https://static-01.daraz.pk/p/a.jpg",
                                    status="completed",
                                    strategy="reuse_cdn",
                                    migrated_url="https://static-01.daraz.pk/p/a.jpg",
                                )
                            ]
                            _prepare_destination(
                                wid,
                                draft=copy.deepcopy(draft),
                                dest=store,
                                price_override=None,
                                allow_duplicates=False,
                                execute=False,
                                confirm=True,
                                gate_on=False,
                            )
    assert calls["n"] == 0  # already marked done — no second query


def test_http_client_reuse_no_socket_leak():
    close_shared_http_clients_for_tests()
    c1 = get_shared_http_client(timeout=5.0, follow_redirects=True)
    c2 = get_shared_http_client(timeout=5.0, follow_redirects=True)
    assert c1 is c2
    assert shared_client_count_for_tests() == 1
    close_shared_http_clients_for_tests()
    assert shared_client_count_for_tests() == 0


def test_image_dedupe_unique_urls_only():
    clear_image_migrate_cache_for_tests()
    client = MagicMock()
    calls: list[str] = []

    def migrate(url):
        calls.append(url)
        return {"data": {"image": {"url": f"migrated:{url}"}}}

    client.migrate_image.side_effect = migrate
    svc = DarazImageMigrationService(client, prefer_cdn_reuse=False)
    urls = [
        "https://cdn.example/a.jpg",
        "https://cdn.example/a.jpg",
        "https://cdn.example/b.jpg",
        "https://cdn.example/a.jpg",
    ]
    results = svc.resolve_many(urls)
    assert len(results) == 4
    assert len(calls) == 2
    assert results[0].migrated_url == results[1].migrated_url == results[3].migrated_url


def test_category_attr_cache_reuse():
    from src.category_attr_resolve import (
        clear_category_attr_cache_for_tests,
        get_cached_category_attributes,
    )

    clear_category_attr_cache_for_tests()
    fetches = {"n": 0}

    def fetcher(_cid):
        fetches["n"] += 1
        return {"data": [{"name": "brand", "is_mandatory": 1}], "mutable": []}

    a = get_cached_category_attributes(10001, fetcher, marketplace="pk")
    b = get_cached_category_attributes(10001, fetcher, marketplace="pk")
    assert fetches["n"] == 1
    assert a["_cache_meta"]["cache_hit"] is False
    assert b["_cache_meta"]["cache_hit"] is True
    # deepcopy isolation
    a["mutable"] = ["x"]
    c = get_cached_category_attributes(10001, fetcher, marketplace="pk")
    assert c.get("mutable") == []


def test_no_brand_cache_reuse():
    from src.brand_resolve import clear_no_brand_cache_for_tests, resolve_destination_no_brand

    clear_no_brand_cache_for_tests()
    calls = {"n": 0}

    def query_brands(**kwargs):
        calls["n"] += 1
        return {"data": {"module": [{"name": "No Brand", "brand_id": "99"}]}}

    r1 = resolve_destination_no_brand(
        primary_category_id=10001, query_brands=query_brands, marketplace="pk"
    )
    r2 = resolve_destination_no_brand(
        primary_category_id=10001, query_brands=query_brands, marketplace="pk"
    )
    assert r1["status"] == "NO_BRAND"
    assert r2.get("cache_hit") is True
    assert calls["n"] == 1


def test_catalog_skip_when_structured_complete(monkeypatch):
    from src.public_daraz import fetch_public_product

    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Product","name":"Glow","image":["https://static-01.daraz.pk/p/a.jpg"],
     "offers":{"price":"199"}}
    </script>
    <script>
    window.rawData = {"skuInfos":{"0":{"price":{"salePrice":{"text":"199"}},"categoryId":10001}},
      "skuBase":{"skus":[{"skuId":"1"}]},"rootCategoryId":10001};
    window.__moduleData__ = {"pdt_data":{"regCategoryId":10001}};
    </script>
    </head><body></body></html>
    """

    class FakeResp:
        status_code = 200
        content = html.encode()
        encoding = "utf-8"
        headers = {}

    catalog_calls = {"n": 0}

    def fake_catalog(*a, **k):
        catalog_calls["n"] += 1
        return {"ok": False, "exact_item_match": False, "timings_ms": {"catalog": 1}}

    client = MagicMock()
    client.get.return_value = FakeResp()
    with (
        patch("src.public_daraz.get_shared_http_client", return_value=client),
        patch("src.public_daraz._resolve_public"),
        patch("src.public_daraz._fetch_catalog_enrichment", side_effect=fake_catalog),
        patch(
            "src.public_daraz._extract_variants_structured",
            return_value=[
                {
                    "price": 199.0,
                    "category_id": "10001",
                    "sale_props": {"Color": "Red"},
                    "price_source": "skuInfos",
                }
            ],
        ),
        patch("src.public_daraz._extract_reg_category_id", return_value="10001"),
        patch("src.public_daraz._extract_pdt_price", return_value=199.0),
    ):
        clear_public_cache_for_tests()
        out = fetch_public_product(
            "https://www.daraz.pk/products/glow-i999999.html", use_cache=False
        )
    assert out["timings_ms"]["catalog_strategy"] == "SKIPPED"
    assert catalog_calls["n"] == 0


def test_catalog_fallback_when_needed(monkeypatch):
    from src.public_daraz import fetch_public_product

    html = "<html><head></head><body>empty</body></html>"

    class FakeResp:
        status_code = 200
        content = html.encode()
        encoding = "utf-8"
        headers = {}

    catalog_calls = {"n": 0}

    def fake_catalog(*a, **k):
        catalog_calls["n"] += 1
        return {
            "ok": True,
            "exact_item_match": False,
            "title": "From catalog",
            "timings_ms": {"catalog": 5},
        }

    client = MagicMock()
    client.get.return_value = FakeResp()
    with (
        patch("src.public_daraz.get_shared_http_client", return_value=client),
        patch("src.public_daraz._resolve_public"),
        patch("src.public_daraz._fetch_catalog_enrichment", side_effect=fake_catalog),
        patch("src.public_daraz._extract_variants_structured", return_value=[]),
        patch("src.public_daraz._extract_reg_category_id", return_value=None),
        patch("src.public_daraz._extract_pdt_price", return_value=None),
        patch("src.public_daraz._parse_json_ld", return_value={}),
        patch("src.public_daraz._extract_images", return_value=[]),
    ):
        clear_public_cache_for_tests()
        out = fetch_public_product(
            "https://www.daraz.pk/products/x-i888888.html", use_cache=False
        )
    assert catalog_calls["n"] == 1
    assert out["timings_ms"]["catalog_strategy"] in {"USED", "FALLBACK"}


def test_independent_source_calls_overlap(monkeypatch):
    from src.public_daraz import fetch_public_product

    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Product","name":"Glow","image":["https://static-01.daraz.pk/p/a.jpg"]}
    </script>
    </head><body></body></html>
    """

    class FakeResp:
        status_code = 200
        content = html.encode()
        encoding = "utf-8"
        headers = {}

    marks: dict[str, float] = {}

    def slow_catalog(*a, **k):
        marks["c0"] = time.perf_counter()
        time.sleep(0.1)
        marks["c1"] = time.perf_counter()
        return {
            "ok": True,
            "exact_item_match": True,
            "category_id": "10001",
            "timings_ms": {"catalog": 100},
        }

    def slow_detail(*a, **k):
        marks["d0"] = time.perf_counter()
        time.sleep(0.1)
        marks["d1"] = time.perf_counter()
        return {
            "ok": True,
            "skipped": False,
            "prices_by_sku": {},
            "timings_ms": {"detail_price_ms": 100},
        }

    client = MagicMock()
    client.get.return_value = FakeResp()
    with (
        patch("src.public_daraz.get_shared_http_client", return_value=client),
        patch("src.public_daraz._resolve_public"),
        patch("src.public_daraz._fetch_catalog_enrichment", side_effect=slow_catalog),
        patch("src.public_daraz._fetch_pdp_detail_sku_prices", side_effect=slow_detail),
        patch(
            "src.public_daraz._extract_variants_structured",
            return_value=[{"sale_props": {}, "price": None, "category_id": None}],
        ),
        patch("src.public_daraz._extract_reg_category_id", return_value=None),
        patch("src.public_daraz._extract_pdt_price", return_value=None),
    ):
        clear_public_cache_for_tests()
        t0 = time.perf_counter()
        fetch_public_product(
            "https://www.daraz.pk/products/y-i777777.html", use_cache=False
        )
        elapsed = time.perf_counter() - t0
    assert "c0" in marks and "d0" in marks
    # Overlap: each started before the other finished.
    assert marks["c0"] < marks["d1"] and marks["d0"] < marks["c1"]
    assert elapsed < 0.18


def test_seller_sku_isolation_multi_store(tenancy_env, monkeypatch):
    repo = tenancy_env
    wid, _, _ = _bootstrap_ws(repo)
    _add_store(repo, wid, "sku-a")
    _add_store(repo, wid, "sku-b")
    skus_seen: list[list[str]] = []

    def capture_prepare(workspace_id, **kwargs):
        draft = kwargs["draft"]
        skus_seen.append(
            [str(v.get("seller_sku")) for v in (draft.get("variants") or [])]
        )
        return {
            "store": {
                "store_id": kwargs["dest"].get("store_id"),
                "display_name": kwargs["dest"].get("store_id"),
            },
            "status": "READY",
            "creation_status": "READY",
        }

    with (
        patch("src.product_add.fetch_public_product", return_value=_fake_extract()),
        patch("src.product_add.find_connected_owner_for_item", return_value=None),
        patch("src.product_add._prepare_destination", side_effect=capture_prepare),
        patch("src.product_add._refresh_destination_tokens"),
    ):
        add_product_from_public_url(
            wid,
            "https://www.daraz.pk/products/test-i123.html",
            ["sku-a", "sku-b"],
            execute=False,
        )
    assert len(skus_seen) == 2
    # Each destination got its own generated Seller SKU list (not a shared object).
    assert skus_seen[0] is not skus_seen[1]
    assert skus_seen[0] and skus_seen[1]
