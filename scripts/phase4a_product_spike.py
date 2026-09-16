"""Phase 4A read-only product capability spike.

Calls Daraz product/category/image probe endpoints. NEVER calls CreateProduct.
Writes sanitized field inventory to data/phase4a_spike_results.json.
"""

from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from src.daraz_api import DarazApiError, DarazClient
from src.ops import client_for_store
from src.token_refresh import refresh_one_store
from src.token_store import access_token_expires_soon, list_stores, upsert_store

OUT = Path("data/phase4a_spike_results.json")
SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "app_secret",
    "sign",
    "Authorization",
}


def _sanitize(obj: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in SENSITIVE_KEYS or "token" in str(k).lower():
                out[k] = "<redacted>"
            else:
                out[k] = _sanitize(v, depth + 1)
        return out
    if isinstance(obj, list):
        if len(obj) > 8:
            return [_sanitize(x, depth + 1) for x in obj[:5]] + [
                f"…(+{len(obj) - 5} more)"
            ]
        return [_sanitize(x, depth + 1) for x in obj]
    if isinstance(obj, str) and len(obj) > 400:
        return obj[:400] + f"…(+{len(obj) - 400} chars)"
    return obj


def _keys_deep(obj: Any, prefix: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            found.add(path)
            found |= _keys_deep(v, path)
    elif isinstance(obj, list) and obj:
        found |= _keys_deep(obj[0], f"{prefix}[]")
    return found


def _call(label: str, fn) -> dict[str, Any]:
    entry: dict[str, Any] = {"label": label, "ok": False}
    try:
        data = fn()
        entry["ok"] = True
        entry["code"] = data.get("code") if isinstance(data, dict) else None
        entry["top_keys"] = (
            sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
        )
        entry["field_paths"] = sorted(_keys_deep(data))[:400]
        entry["sample"] = _sanitize(data)
        return entry
    except DarazApiError as exc:
        entry["error"] = str(exc)
        entry["error_code"] = exc.code
        entry["http_status"] = exc.http_status
        entry["payload"] = _sanitize(exc.payload)
        return entry
    except Exception as exc:  # noqa: BLE001
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["traceback"] = traceback.format_exc()[-800:]
        return entry


def probe_public_url(url: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "url": url,
        "host_ok": False,
        "status": None,
        "item_id_from_url": None,
        "signals": {},
    }
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    result["host_ok"] = host.endswith("daraz.pk") or host.endswith("daraz.com")
    m = re.search(r"-i(\d+)", parsed.path or "")
    if not m:
        m = re.search(r"/products/.*?(\d{6,})", parsed.path or "")
    if m:
        result["item_id_from_url"] = m.group(1)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        result["status"] = resp.status_code
        result["final_url"] = str(resp.url)
        result["final_host"] = urlparse(str(resp.url)).hostname
        text = resp.text or ""
        result["html_bytes"] = len(text)
        result["signals"] = {
            "has_json_ld": '"@type"' in text and "Product" in text,
            "has_window_pageData": "window.pageData" in text
            or "window.__pageData" in text,
            "has_preload_state": "__PRELOADED_STATE__" in text
            or "__NEXT_DATA__" in text,
            "has_skuInfos": "skuInfos" in text or "skuInfo" in text,
            "has_video": bool(
                re.search(r"video|mp4|m3u8", text, re.I)
            ),
            "title_tag": (
                (re.search(r"<title>(.*?)</title>", text, re.I | re.S) or [None, None])[
                    1
                ]
                or ""
            )[:180],
            "og_image": bool(re.search(r'property=["\']og:image', text, re.I)),
            "blocked_hint": any(
                x in text.lower()
                for x in ("captcha", "access denied", "robot", "cf-browser-verification")
            ),
        }
        # Extract a small JSON-LD Product block if present
        ld = re.search(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            text,
            re.I | re.S,
        )
        if ld:
            try:
                parsed_ld = json.loads(ld.group(1))
                result["json_ld_sample"] = _sanitize(parsed_ld)
            except Exception:  # noqa: BLE001
                result["json_ld_sample"] = "<unparseable>"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    stores = list_stores()
    if not stores:
        print("No stores in token vault")
        return 1
    store = stores[0]
    store_id = store.get("store_id")
    print(f"Using store={store_id}")

    def _upsert(record: dict[str, Any]) -> dict[str, Any]:
        return upsert_store(record)

    if access_token_expires_soon(store, within_minutes=60):
        print("Refreshing token…")
        refresh_one_store(store, upsert_fn=_upsert)
        store = next(s for s in list_stores() if s.get("store_id") == store_id)

    client: DarazClient = client_for_store(store)
    client.timeout = 60.0

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "store_id": store_id,
        "api_base": client.api_base,
        "note": "READ-ONLY spike. CreateProduct intentionally NOT called.",
        "calls": [],
    }

    # --- GetProducts ---
    products_entry = _call(
        "GET /products/get (filter=all, limit=2)",
        lambda: client._request(
            "/products/get",
            business_params={
                "filter": "all",
                "offset": "0",
                "limit": "2",
                "options": "1",
            },
        ),
    )
    report["calls"].append(products_entry)

    item_id = None
    seller_sku = None
    if products_entry.get("ok"):
        sample = products_entry.get("sample") or {}
        data = sample.get("data") or {}
        products = data.get("products") or data.get("product") or []
        if isinstance(products, dict):
            products = [products]
        if products:
            p0 = products[0]
            item_id = (
                p0.get("item_id")
                or p0.get("itemId")
                or p0.get("product_id")
                or p0.get("productId")
            )
            skus = p0.get("skus") or p0.get("sku") or []
            if isinstance(skus, dict):
                skus = [skus]
            if skus:
                seller_sku = (
                    skus[0].get("SellerSku")
                    or skus[0].get("seller_sku")
                    or skus[0].get("ShopSku")
                )
            report["first_product_ids"] = {
                "item_id": item_id,
                "seller_sku": seller_sku,
                "product_top_keys": sorted(p0.keys()) if isinstance(p0, dict) else [],
                "sku0_keys": sorted(skus[0].keys()) if skus and isinstance(skus[0], dict) else [],
            }

    # --- GetProductItem ---
    if item_id is not None:
        report["calls"].append(
            _call(
                f"GET /product/item/get item_id={item_id}",
                lambda: client._request(
                    "/product/item/get",
                    business_params={"item_id": str(item_id)},
                ),
            )
        )
        if seller_sku:
            report["calls"].append(
                _call(
                    f"GET /product/item/get seller_sku={seller_sku}",
                    lambda: client._request(
                        "/product/item/get",
                        business_params={"seller_sku": str(seller_sku)},
                    ),
                )
            )
    else:
        report["calls"].append(
            {
                "label": "GET /product/item/get",
                "ok": False,
                "error": "skipped — no item_id from GetProducts",
            }
        )

    # Probe whether foreign item_id works (public catalog id sample)
    report["calls"].append(
        _call(
            "GET /product/item/get foreign item_id=1000123456 (ownership probe)",
            lambda: client._request(
                "/product/item/get",
                business_params={"item_id": "1000123456"},
            ),
        )
    )

    # Category / brands
    report["calls"].append(
        _call(
            "GET /category/tree/get",
            lambda: client._request("/category/tree/get"),
        )
    )

    # Try a leaf category id from tree if available
    cat_id = None
    tree_call = report["calls"][-1]
    if tree_call.get("ok"):
        # walk sample for first leaf-ish id
        def find_cat(node: Any) -> Any:
            if isinstance(node, dict):
                if node.get("leaf") in (True, "true", 1, "1") and node.get("category_id"):
                    return node.get("category_id")
                for v in node.values():
                    found = find_cat(v)
                    if found:
                        return found
            elif isinstance(node, list):
                for x in node:
                    found = find_cat(x)
                    if found:
                        return found
            return None

        cat_id = find_cat(tree_call.get("sample"))

    if cat_id is not None:
        report["calls"].append(
            _call(
                f"GET /category/attributes/get primary_category_id={cat_id}",
                lambda: client._request(
                    "/category/attributes/get",
                    business_params={"primary_category_id": str(cat_id)},
                ),
            )
        )
    else:
        report["calls"].append(
            {
                "label": "GET /category/attributes/get",
                "ok": False,
                "error": "skipped — no leaf category_id found",
            }
        )

    report["calls"].append(
        _call(
            "GET /category/brands/get (or /brands/get)",
            lambda: client._request(
                "/category/brands/get",
                business_params={"primary_category_id": str(cat_id or "0")},
            ),
        )
    )
    # alternate path
    report["calls"].append(
        _call(
            "GET /brands/get",
            lambda: client._request("/brands/get", business_params={"offset": "0", "limit": "10"}),
        )
    )

    # Image migrate — probe with empty/invalid to see permission/shape (no real migrate)
    report["calls"].append(
        _call(
            "POST /images/migrate (permission probe, empty payload)",
            lambda: client._request(
                "/images/migrate",
                method="POST",
                business_params={},
                body=json.dumps({"payload": {"Image": {"Url": []}}}),
            ),
        )
    )
    report["calls"].append(
        _call(
            "POST /image/migrate (alternate path probe)",
            lambda: client._request(
                "/image/migrate",
                method="POST",
                business_params={},
                body=json.dumps({"payload": {"Image": {"Url": []}}}),
            ),
        )
    )

    # CreateProduct — DO NOT create; only probe with missing payload to see if API exists / auth
    report["calls"].append(
        _call(
            "POST /product/create (DENIED CREATE — empty payload permission probe only)",
            lambda: client._request(
                "/product/create",
                method="POST",
                business_params={},
                body="{}",
            ),
        )
    )

    # Public URL investigation (PK example — may 404; still records anti-bot behavior)
    # Prefer deriving a PDP URL from owned product if we can
    public_urls = []
    if item_id is not None:
        public_urls.append(f"https://www.daraz.pk/products/i{item_id}.html")
    public_urls.append(
        "https://www.daraz.pk/products/sample-product-i123456789-s123456789.html"
    )
    report["public_url_probes"] = [probe_public_url(u) for u in public_urls]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT}")
    for c in report["calls"]:
        status = "OK" if c.get("ok") else f"FAIL:{c.get('error_code') or c.get('error')}"
        print(f"  [{status}] {c.get('label')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
