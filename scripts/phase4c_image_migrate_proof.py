"""Phase 4C — prove corrected migrate+poll and CDN reuse (owned image only)."""

from __future__ import annotations

import json
from pathlib import Path

from src.daraz_api import DarazClient
from src.image_migrate import DarazImageMigrationService, is_daraz_product_cdn_url
from src.token_store import list_stores

OUT = Path("data/phase4c_image_migrate_proof.json")


def main() -> None:
    stores = list_stores()
    if not stores:
        raise SystemExit("no stores")
    client = DarazClient(access_token=stores[0]["access_token"])
    prods = client.get_products(limit=1, filter="live")
    products = (prods.get("data") or {}).get("products") or []
    if not products or not (products[0].get("images") or []):
        raise SystemExit("no image")
    url = products[0]["images"][0]

    svc = DarazImageMigrationService(client, prefer_cdn_reuse=True)
    reuse = svc.resolve_one(url)

    svc2 = DarazImageMigrationService(client, prefer_cdn_reuse=False, timeout_s=30)
    migrated = svc2.resolve_one(url, force_migrate=True)

    # Batch path explicitly
    batch_submit = client.migrate_images([url])
    batch_id = batch_submit.get("batch_id")
    import time

    time.sleep(0.8)
    poll = client.get_image_response(str(batch_id))
    poll_url = None
    data = poll.get("data") if isinstance(poll.get("data"), dict) else {}
    images = data.get("images") if isinstance(data, dict) else None
    if isinstance(images, list) and images:
        poll_url = (images[0] or {}).get("url")

    proof = {
        "source_url": url,
        "is_daraz_cdn": is_daraz_product_cdn_url(url),
        "cdn_reuse": {
            "status": reuse.status,
            "strategy": reuse.strategy,
            "url": reuse.migrated_url,
        },
        "singular_or_batch_force": {
            "status": migrated.status,
            "strategy": migrated.strategy,
            "url": migrated.migrated_url,
            "batch_id": migrated.batch_id,
            "error": migrated.error,
        },
        "batch_poll": {
            "batch_id": batch_id,
            "poll_ok": str(poll.get("code")) == "0",
            "final_url": poll_url,
            "images_count": len(images) if isinstance(images, list) else 0,
        },
        "final_image_url_proof": "PROVEN" if poll_url else "NOT_PROVEN",
        "cdn_reuse_proof": "PROVEN" if reuse.strategy == "reuse_cdn" else "NOT_PROVEN",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
