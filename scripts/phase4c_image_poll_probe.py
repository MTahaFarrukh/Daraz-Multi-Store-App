"""Phase 4C — diagnose /image/response/get E005 and singular migrate URL return.

Safe: owned-store CDN image only. Does not create products. Does not log tokens.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.daraz_api import DarazApiError, DarazClient
from src.token_store import list_stores

OUT = Path("data/phase4c_image_poll_probe.json")


def _safe(data: dict) -> dict:
    keep = {}
    for k in ("code", "batch_id", "request_id", "type", "message"):
        if k in data:
            keep[k] = data[k]
    d = data.get("data")
    if isinstance(d, dict):
        keep["data_keys"] = list(d.keys())
        img = d.get("image")
        if isinstance(img, dict) and img.get("url"):
            keep["image_url"] = img["url"]
        imgs = d.get("images")
        if isinstance(imgs, list) and imgs:
            first = imgs[0] if isinstance(imgs[0], dict) else {}
            if first.get("url"):
                keep["first_image_url"] = first["url"]
            keep["images_count"] = len(imgs)
        if d.get("batch_id"):
            keep["data_batch_id"] = d["batch_id"]
    return keep


def main() -> None:
    stores = list_stores()
    if not stores:
        raise SystemExit("no stores")
    client = DarazClient(access_token=stores[0]["access_token"])

    prods = client.get_products(limit=1, filter="live")
    products = (prods.get("data") or {}).get("products") or []
    if not products or not (products[0].get("images") or []):
        raise SystemExit("no product image")
    img = products[0]["images"][0]
    item_id = products[0].get("item_id")

    results: dict = {
        "source_image": img,
        "item_id": item_id,
        "migrates": {},
        "polls": {},
    }

    xml_image = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Request><Image><Url>{img}</Url></Image></Request>"
    )
    xml_images = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Request><Images><Url>{img}</Url></Images></Request>"
    )

    migrate_cases = [
        ("images_migrate_Image", "/images/migrate", xml_image),
        ("images_migrate_Images", "/images/migrate", xml_images),
        ("image_migrate_Image", "/image/migrate", xml_image),
        ("image_migrate_Images", "/image/migrate", xml_images),
    ]

    batch_ids: list[tuple[str, str]] = []
    for label, path, xml in migrate_cases:
        try:
            data = client._request(path, method="POST", business_params={"payload": xml})
            safe = _safe(data)
            results["migrates"][label] = {"ok": True, **safe}
            bid = data.get("batch_id") or (
                (data.get("data") or {}).get("batch_id")
                if isinstance(data.get("data"), dict)
                else None
            )
            if bid:
                batch_ids.append((label, str(bid)))
        except DarazApiError as exc:
            results["migrates"][label] = {
                "ok": False,
                "code": exc.code,
                "msg": str(exc)[:200],
                **_safe(exc.payload or {}),
            }

    # Prefer batch from correct <Images> migrate if available
    preferred = None
    for label, bid in batch_ids:
        if "Images" in label:
            preferred = (label, bid)
            break
    if not preferred and batch_ids:
        preferred = batch_ids[0]

    if preferred:
        src_label, batch = preferred
        results["poll_batch_from"] = src_label
        results["poll_batch_id"] = batch
        time.sleep(1.0)

        poll_cases = [
            ("GET_/image/response/get_batch_id", "GET", "/image/response/get", {"batch_id": batch}),
            ("GET_/image/response/get_batchId", "GET", "/image/response/get", {"batchId": batch}),
            ("POST_/image/response/get_batch_id", "POST", "/image/response/get", {"batch_id": batch}),
            ("GET_/images/response/get_batch_id", "GET", "/images/response/get", {"batch_id": batch}),
            (
                "GET_/image/response/get_payload_xml",
                "GET",
                "/image/response/get",
                {
                    "payload": (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        f"<Request><BatchId>{batch}</BatchId></Request>"
                    )
                },
            ),
        ]
        for label, method, path, bp in poll_cases:
            try:
                data = client._request(path, method=method, business_params=bp)
                results["polls"][label] = {"ok": True, **_safe(data)}
            except DarazApiError as exc:
                results["polls"][label] = {
                    "ok": False,
                    "code": exc.code,
                    "msg": str(exc)[:200],
                    **_safe(exc.payload or {}),
                }

        # Retry successful path once after extra wait if any ok
        time.sleep(2.0)
        try:
            data = client._request(
                "/image/response/get",
                method="GET",
                business_params={"batch_id": batch},
            )
            results["polls"]["retry_GET_batch_id_after_3s"] = {"ok": True, **_safe(data)}
        except DarazApiError as exc:
            results["polls"]["retry_GET_batch_id_after_3s"] = {
                "ok": False,
                "code": exc.code,
                "msg": str(exc)[:200],
                **_safe(exc.payload or {}),
            }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2)[:5000])
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
