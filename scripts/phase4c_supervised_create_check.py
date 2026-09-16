"""Phase 4C — supervised CreateProduct gate check (does not create by default).

Usage:
  ALLOW_PRODUCT_CREATE_PROBE=true \\
  python -m scripts.phase4c_supervised_create_check

Requires two connected stores for a real A→B create. With fewer stores: NOT RUN.
Never executes CreateProduct unless --execute is passed AND env flag is on.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.token_store import list_stores, sanitize_store_view

OUT = Path("data/phase4c_supervised_create.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Actually call CreateProduct")
    parser.add_argument("--source-product-id", default="")
    parser.add_argument("--destination-store-id", default="")
    args = parser.parse_args()

    stores = list_stores()
    safe_stores = [sanitize_store_view(s) for s in stores]
    report: dict = {
        "connected_stores": len(stores),
        "stores": [
            {"store_id": s.get("store_id"), "display_name": s.get("display_name")}
            for s in safe_stores
        ],
        "supervised_create": None,
    }

    if len(stores) < 2:
        report["supervised_create"] = {
            "status": "NOT_RUN",
            "reason": (
                "No safe second connected destination store available "
                f"(found {len(stores)}). Do not create anything."
            ),
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return

    if not args.source_product_id or not args.destination_store_id:
        report["supervised_create"] = {
            "status": "NOT_RUN",
            "reason": (
                "Two stores present but --source-product-id and "
                "--destination-store-id were not provided. Refusing to invent a create."
            ),
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return

    # Operator path would call run_supervised_create here with workspace context.
    report["supervised_create"] = {
        "status": "NOT_RUN",
        "reason": (
            "Multi-store path requires authenticated workspace API invocation; "
            "use POST /api/products/{id}/create-probe with confirm+execute under "
            "ALLOW_PRODUCT_CREATE_PROBE. Script refuses silent create."
        ),
        "execute_requested": args.execute,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
