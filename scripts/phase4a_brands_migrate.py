"""Quick probes: brands startRow, migrate response shape, 5-SKU product."""

from __future__ import annotations

import json
from pathlib import Path

from src.daraz_api import DarazApiError
from src.ops import client_for_store
from src.token_store import list_stores

OUT = Path("data/phase4a_spike_brands_migrate.json")


def main() -> int:
    client = client_for_store(list_stores()[0])
    client.timeout = 60.0
    out: dict = {}

    for params in [
        {"primary_category_id": "10002730", "startRow": "0", "pageSize": "20"},
        {"primary_category_id": "10002730", "startRow": "0", "limit": "20"},
        {"startRow": "0", "pageSize": "20", "name": "No Brand"},
    ]:
        try:
            data = client._request("/category/brands/query", business_params=params)
            d = data.get("data")
            out[f"brands_{params}"] = {
                "ok": True,
                "data_type": type(d).__name__,
                "keys": sorted(d.keys()) if isinstance(d, dict) else None,
                "len": len(d) if isinstance(d, list) else None,
                "sample": d[:2] if isinstance(d, list) else (d if isinstance(d, dict) else str(d)[:300]),
            }
            print("OK brands", params)
        except DarazApiError as exc:
            out[f"brands_{params}"] = {"ok": False, "code": exc.code, "msg": str(exc)}
            print("FAIL brands", params, exc.code, str(exc)[:120])

    migrate_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Request><Image>"
        "<Url>https://static-01.daraz.pk/p/b35e75a2b2609e4071729383467e6f6c.png</Url>"
        "</Image></Request>"
    )
    try:
        data = client._request(
            "/images/migrate",
            method="POST",
            business_params={"payload": migrate_xml},
        )
        out["migrate"] = data
        print("MIGRATE OK", json.dumps(data)[:500])
        batch = None
        if isinstance(data.get("data"), dict):
            batch = data["data"].get("batch_id") or data["data"].get("request_id")
        if batch:
            try:
                resp = client._request(
                    "/image/response/get", business_params={"batch_id": str(batch)}
                )
                out["migrate_response"] = resp
                print("RESPONSE OK", json.dumps(resp)[:500])
            except DarazApiError as exc:
                out["migrate_response"] = {"code": exc.code, "msg": str(exc), "payload": exc.payload}
                print("RESPONSE FAIL", exc.code, str(exc)[:160])
    except DarazApiError as exc:
        out["migrate"] = {"code": exc.code, "msg": str(exc), "payload": exc.payload}
        print("MIGRATE FAIL", exc.code)

    # 5-SKU product
    detail = client._request(
        "/product/item/get", business_params={"item_id": "1962226787"}
    )
    d = detail["data"]
    out["five_sku"] = {
        "item_id": d.get("item_id"),
        "variation": d.get("variation"),
        "sku_count": len(d.get("skus") or []),
        "skus": [
            {
                "SellerSku": s.get("SellerSku"),
                "saleProp": s.get("saleProp"),
                "price": s.get("price"),
                "quantity": s.get("quantity"),
                "package_weight": s.get("package_weight"),
                "package_length": s.get("package_length"),
                "package_width": s.get("package_width"),
                "package_height": s.get("package_height"),
                "Images": s.get("Images"),
            }
            for s in (d.get("skus") or [])
        ],
        "attr_keys": sorted((d.get("attributes") or {}).keys()),
        "images_count": len(d.get("images") or []),
        "video": (d.get("attributes") or {}).get("video"),
        "desc_has_img": "<img" in ((d.get("attributes") or {}).get("description_en") or "").lower(),
    }
    print("five_sku", out["five_sku"]["sku_count"], "variation", out["five_sku"]["variation"])

    # Foreign public item via seller API using item id extracted from another store's typical URL pattern
    # Use a well-known public item if we can find one from HTML of homepage - skip; already know 207.

    # SellerSku length samples
    skus = [s["SellerSku"] for s in out["five_sku"]["skus"] if s.get("SellerSku")]
    skus += ["MTF-POPSICLE-Squishy", "MTF-BUTTER-SQUISHY"]
    out["seller_sku_samples"] = [{"sku": s, "len": len(s)} for s in skus]
    print("sku lens", out["seller_sku_samples"])

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
