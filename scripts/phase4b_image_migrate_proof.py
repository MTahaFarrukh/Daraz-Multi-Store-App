"""Live image migration poll proof (no CreateProduct)."""

from __future__ import annotations

import json
from pathlib import Path

from src.image_migrate import DarazImageMigrationService
from src.ops import client_for_store
from src.token_store import list_stores

OUT = Path("data/phase4b_image_migrate_proof.json")


def main() -> int:
    stores = list_stores()
    if not stores:
        print("No stores")
        return 1
    client = client_for_store(stores[0])
    client.timeout = 60.0
    # Owned CDN image from 4A spike
    url = "https://static-01.daraz.pk/p/b35e75a2b2609e4071729383467e6f6c.png"
    svc = DarazImageMigrationService(client, poll_interval_s=2.0, timeout_s=30.0)
    result = svc.migrate_one(url)
    payload = {
        "source_url": result.source_url,
        "status": result.status,
        "batch_id": result.batch_id,
        "migrated_url": result.migrated_url,
        "error": result.error,
        "raw_keys": sorted((result.raw or {}).keys()),
        "raw_sample": result.raw,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("status", "batch_id", "migrated_url", "error")}, indent=2))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
