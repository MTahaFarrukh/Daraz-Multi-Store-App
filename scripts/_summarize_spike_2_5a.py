"""Summarize spike_2_5a_results.json without secrets."""
from __future__ import annotations

import json
from pathlib import Path

p = Path("data/spike_2_5a_results.json")
d = json.loads(p.read_text(encoding="utf-8"))
print("store", d["store"]["store_id"], d["store"]["display_name"])
for pr in d["probes"]:
    print("\n==", pr.get("probe"), "==")
    for k in (
        "ok",
        "api_code",
        "countTotal",
        "count",
        "page_len",
        "item_count",
        "overlap_with_page0",
        "order_fields",
        "item_fields",
        "money_related_fields",
        "sample_item_money",
        "item_status_counts",
        "data_keys",
        "message",
        "error",
    ):
        if k in pr and pr[k] not in (None, [], {}):
            val = pr[k]
            if k == "sample_order":
                continue
            print(f"  {k}: {json.dumps(val, default=str)[:500]}")
    if pr.get("sample_order"):
        so = pr["sample_order"]
        keep = {
            k: so.get(k)
            for k in (
                "order_id",
                "order_number",
                "price",
                "items_count",
                "statuses",
                "created_at",
                "updated_at",
                "payment_method",
                "shipping_fee",
                "voucher",
                "voucher_platform",
                "voucher_seller",
            )
            if k in so
        }
        print("  sample_order_moneyish:", json.dumps(keep, default=str))
    if pr.get("data_sample") and not pr.get("sample_order"):
        ds = pr["data_sample"]
        if isinstance(ds, dict):
            print("  data_sample_keys:", sorted(ds.keys())[:40])
            # finance rows often nested
            for nk in ("transactions", "transaction_details", "payout_status", "data"):
                if nk in ds and isinstance(ds[nk], list) and ds[nk]:
                    row = ds[nk][0]
                    if isinstance(row, dict):
                        print(f"  first_{nk}_keys:", sorted(row.keys()))
                        print(f"  first_{nk}_sample:", json.dumps({k: row.get(k) for k in list(row)[:25]}, default=str)[:600])
        elif isinstance(ds, list) and ds:
            row = ds[0]
            if isinstance(row, dict):
                print("  list0_keys:", sorted(row.keys()))
                print("  list0_sample:", json.dumps({k: row.get(k) for k in list(row)[:30]}, default=str)[:600])
