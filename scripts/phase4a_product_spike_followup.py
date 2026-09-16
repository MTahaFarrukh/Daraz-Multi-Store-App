"""Phase 4A follow-up probes — still no intentional CreateProduct."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from src.daraz_api import DarazApiError
from src.ops import client_for_store
from src.token_store import list_stores

OUT = Path("data/phase4a_spike_followup.json")


def scrub(obj, depth=0):
    if depth > 10:
        return "…"
    if isinstance(obj, dict):
        return {k: scrub(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        head = [scrub(x, depth + 1) for x in obj[:8]]
        if len(obj) > 8:
            head.append(f"+{len(obj) - 8}")
        return head
    if isinstance(obj, str) and len(obj) > 600:
        return obj[:600] + "…"
    return obj


def main() -> int:
    store = list_stores()[0]
    client = client_for_store(store)
    client.timeout = 60.0
    out: dict = {"calls": [], "notes": []}

    def call(label, fn):
        entry = {"label": label, "ok": False}
        try:
            data = fn()
            entry["ok"] = True
            entry["code"] = data.get("code")
            d = data.get("data")
            if isinstance(d, dict):
                entry["data_keys"] = sorted(d.keys())
                if "products" in d:
                    entry["product_count"] = len(d.get("products") or [])
                    entry["total_products"] = d.get("total_products")
            elif isinstance(d, list) and d and isinstance(d[0], dict):
                entry["list_len"] = len(d)
                entry["first_item_keys"] = sorted(d[0].keys())
            out["calls"].append(entry)
            print("OK", label)
            return data
        except DarazApiError as exc:
            entry["error"] = str(exc)
            entry["error_code"] = exc.code
            entry["payload"] = scrub(exc.payload)
            out["calls"].append(entry)
            print("FAIL", label, exc.code, str(exc)[:140])
            return None
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
            out["calls"].append(entry)
            print("ERR", label, exc)
            return None

    # Correct leaf category for live product
    attr_data = call(
        "category/attributes primary_category_id=10002730",
        lambda: client._request(
            "/category/attributes/get",
            business_params={"primary_category_id": "10002730"},
        ),
    )
    if attr_data:
        rows = attr_data.get("data") or []
        summary = []
        for a in rows:
            summary.append(
                {
                    "name": a.get("name") or a.get("attribute_name") or a.get("label"),
                    "id": a.get("id") or a.get("attribute_id"),
                    "is_mandatory": a.get("is_mandatory"),
                    "attribute_type": a.get("attribute_type") or a.get("attr_type"),
                    "input_type": a.get("input_type"),
                    "is_sale_prop": a.get("is_sale_prop"),
                    "options_count": (
                        len(a.get("options") or a.get("values") or [])
                        if isinstance(a.get("options") or a.get("values"), list)
                        else None
                    ),
                }
            )
        out["category_10002730_attr_summary"] = summary
        out["category_10002730_mandatory"] = [
            x for x in summary if str(x.get("is_mandatory")) in ("1", "True", "true")
        ]
        print(
            "attrs",
            len(summary),
            "mandatory",
            len(out["category_10002730_mandatory"]),
        )
        for x in out["category_10002730_mandatory"]:
            print("  MANDATORY", x)

    resp = call(
        "products/get filter=all limit=50 offset=0",
        lambda: client._request(
            "/products/get",
            business_params={
                "filter": "all",
                "limit": "50",
                "offset": "0",
                "options": "1",
            },
        ),
    )
    multi = []
    package_content_hits = 0
    video_hits = 0
    if resp:
        for p in (resp.get("data") or {}).get("products") or []:
            attrs = p.get("attributes") or {}
            if attrs.get("package_content") or attrs.get("package_content_en"):
                package_content_hits += 1
            if attrs.get("video"):
                video_hits += 1
            skus = p.get("skus") or []
            sale_nonempty = any((s.get("saleProp") or {}) for s in skus)
            if len(skus) > 1 or sale_nonempty:
                multi.append(
                    {
                        "item_id": p.get("item_id"),
                        "sku_count": len(skus),
                        "saleProps": [s.get("saleProp") for s in skus[:5]],
                        "variation": p.get("variation"),
                        "SellerSkus": [s.get("SellerSku") for s in skus[:8]],
                        "has_video": bool(attrs.get("video")),
                    }
                )
    out["multi_variant_hits"] = multi[:10]
    out["scan_stats"] = {
        "scanned": 50,
        "multi_variant": len(multi),
        "package_content_hits": package_content_hits,
        "video_attr_hits": video_hits,
    }
    print("multi_variant_hits", len(multi), "video", video_hits, "pkg_content", package_content_hits)

    if multi:
        iid = multi[0]["item_id"]
        detail = call(
            f"product/item/get multi item_id={iid}",
            lambda: client._request(
                "/product/item/get", business_params={"item_id": str(iid)}
            ),
        )
        if detail:
            d = detail.get("data") or {}
            out["multi_detail_summary"] = {
                "item_id": d.get("item_id"),
                "variation": d.get("variation"),
                "sku_count": len(d.get("skus") or []),
                "saleProps": [(s.get("SellerSku"), s.get("saleProp")) for s in (d.get("skus") or [])[:8]],
                "attr_keys": sorted((d.get("attributes") or {}).keys()),
                "video": (d.get("attributes") or {}).get("video"),
            }

    for path, params in [
        ("/category/brands/query", {"primary_category_id": "10002730", "offset": "0", "limit": "20"}),
        ("/product/brands/get", {"primary_category_id": "10002730"}),
        ("/brands/get", {"q": "No Brand", "offset": "0", "limit": "10"}),
        ("/category/suggestion/get", {"product_name": "squishy toy"}),
        ("/product/category/suggestion/get", {"product_name": "squishy toy"}),
    ]:
        call(
            f"GET {path}",
            lambda path=path, params=params: client._request(path, business_params=params),
        )

    # Lazada-style XML payload as query business param — empty product = expect validation error, not create
    xml_empty = '<?xml version="1.0" encoding="UTF-8"?><Request><Product></Product></Request>'
    call(
        "POST /product/create payload=emptyXML (validation probe only)",
        lambda: client._request(
            "/product/create",
            method="POST",
            business_params={"payload": xml_empty},
        ),
    )

    migrate_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Request><Image>"
        "<Url>https://static-01.daraz.pk/p/b35e75a2b2609e4071729383467e6f6c.png</Url>"
        "</Image></Request>"
    )
    call(
        "POST /images/migrate payload=oneOwnedCdnUrl",
        lambda: client._request(
            "/images/migrate",
            method="POST",
            business_params={"payload": migrate_xml},
        ),
    )
    call(
        "POST /image/migrate payload=oneOwnedCdnUrl",
        lambda: client._request(
            "/image/migrate",
            method="POST",
            business_params={"payload": migrate_xml},
        ),
    )
    call(
        "GET /image/response/get (probe)",
        lambda: client._request(
            "/image/response/get",
            business_params={"batch_id": "0"},
        ),
    )

    # Deeper public PDP
    url = "https://www.daraz.pk/products/i1974026524.html"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(timeout=30.0, follow_redirects=True) as http:
        r = http.get(url, headers=headers)
    html = r.text
    pub = {"status": r.status_code, "bytes": len(html), "final_url": str(r.url)}
    for name, pat in [
        ("has_app_run", r"app\.run\("),
        ("has_skuInfos", r'"skuInfos"'),
        ("has_pdpTrackingData", r"pdpTrackingData"),
        ("has_moduleData", r"moduleData"),
        ("has_json_ld", r'application/ld\+json'),
    ]:
        pub[name] = bool(re.search(pat, html, re.I))
    pub["mentions_package_weight"] = (
        "package_weight" in html or "Package Weight" in html
    )
    pub["mentions_package_content"] = (
        "package_content" in html
        or "What's in the box" in html
        or "Package Content" in html
    )
    pub["img_cdn_count"] = len(
        re.findall(r"static-01\.daraz\.pk/p/[a-f0-9]+\.(?:jpg|png|webp)", html)
    )
    pub["video_id_hits"] = re.findall(r'"video(?:Id|_id)?"\s*:\s*"?(\d{6,})"?', html)[
        :10
    ]
    pub["blocked_hint"] = any(
        x in html.lower() for x in ("captcha", "access denied", "cf-browser")
    )
    # Try extract skuInfos blob size
    m = re.search(r'"skuInfos"\s*:\s*(\{)', html)
    pub["skuInfos_anchor"] = bool(m)
    out["public_deep"] = pub
    print("public_deep", pub)

    OUT.write_text(json.dumps(scrub(out), indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
