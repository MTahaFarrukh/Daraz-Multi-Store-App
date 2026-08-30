"""Probe Daraz label APIs for one ready-to-ship order (local diagnostic)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as: python scripts/probe_label_sources.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.daraz_api import DarazApiError
from src.label_adapter import document_from_daraz_response, document_from_print_awb_response
from src.ops import client_for_store, default_created_after, resolve_stores
from src.orders import extract_order_items, extract_orders, order_label_meta


def main() -> int:
    stores = resolve_stores()
    store = stores[0]
    client = client_for_store(store)
    created = default_created_after()

    resp = client.get_orders(
        created_after=created,
        status="ready_to_ship",
        limit=3,
        offset=0,
    )
    orders = extract_orders(resp)
    if not orders:
        print("No ready_to_ship orders found.")
        return 1

    order = orders[0]
    order_id = order.get("order_id")
    print(f"Order: {order_id}")

    items_resp = client.get_order_items(order_id)
    items = extract_order_items(items_resp)
    item_ids, package_id = order_label_meta(items)
    print(f"Item IDs: {item_ids}")
    print(f"Package ID: {package_id or '(none)'}")

    if items:
        sample = items[0]
        keys = sorted(k for k in sample if "pack" in k.lower() or "track" in k.lower())
        print(f"Pack/tracking fields on first item: {json.dumps({k: sample.get(k) for k in keys}, default=str)}")

    if package_id:
        print("\n--- PrintAWB (doc_type=PDF) ---")
        try:
            awb_resp = client.get_package_shipping_label(package_id, doc_type="PDF")
            label = document_from_print_awb_response(
                awb_resp,
                store_id=str(store.get("store_id")),
                store_name=str(store.get("store_name")),
                order_id=str(order_id),
                order_item_ids=item_ids,
                download_url=client.download_binary_url,
            )
            print(f"PrintAWB is_pdf={label.is_pdf()} mime={label.normalized_mime_type()}")
            print(f"PrintAWB response keys: {list(awb_resp.keys())}")
        except DarazApiError as exc:
            print(f"PrintAWB FAILED: [{exc.code}] {exc}")

    print("\n--- GetDocument (shippingLabel) ---")
    try:
        doc_resp = client.get_shipping_label(item_ids)
        document = (doc_resp.get("data") or {}).get("document") or {}
        mime = document.get("mime_type") or document.get("MimeType")
        label = document_from_daraz_response(
            doc_resp,
            store_id=str(store.get("store_id")),
            store_name=str(store.get("store_name")),
            order_id=str(order_id),
            order_item_ids=item_ids,
        )
        print(f"GetDocument mime={mime} is_pdf={label.is_pdf()} is_html={label.is_html()}")
    except DarazApiError as exc:
        print(f"GetDocument FAILED: [{exc.code}] {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
