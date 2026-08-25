"""
Phase 3 CLI - multi-store orders, labels, and combined PDF printing.

Commands:
  list-stores
  refresh-tokens
  fetch-orders
  fetch-labels
  print-all
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import DEFAULT_API_BASE, get_env, require_env
from src.daraz_api import DarazApiError, DarazClient
from src.label_adapter import document_from_daraz_response
from src.label_processor import (
    LABELS_DIR,
    OUTPUT_DIR,
    LabelDocument,
    load_label_from_file,
    merge_labels,
    pdf_page_count,
)
from src.orders import (
    eligible_item_ids,
    extract_order_items,
    extract_orders,
    order_preview,
)
from src.token_refresh import refresh_store_tokens
from src.token_store import (
    get_store,
    list_sanitized_stores,
    list_stores,
)


def _default_created_after(days: int = 30) -> str:
    dt = datetime.now(UTC) - timedelta(days=days)
    # Daraz expects offset times; use +05:00 for PK POC convenience.
    return dt.astimezone().replace(microsecond=0).isoformat()


def _resolve_stores(store_id: str | None) -> list[dict[str, Any]]:
    if store_id:
        store = get_store(store_id)
        if not store:
            raise SystemExit(f"Unknown store: {store_id}")
        return [store]
    stores = list_stores()
    if not stores:
        raise SystemExit("No stores connected. Run OAuth via /oauth/login first.")
    return stores


def _cap_orders(orders: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Daraz may ignore limit; enforce client-side cap for safety."""
    if limit <= 0:
        return orders
    return orders[:limit]


def _client_for_store(store: dict[str, Any]) -> DarazClient:
    token = store.get("access_token")
    if not token:
        raise SystemExit(f"Store {store.get('store_id')} has no access_token")
    return DarazClient(
        app_key=require_env("DARAZ_APP_KEY"),
        app_secret=require_env("DARAZ_APP_SECRET"),
        access_token=str(token),
        api_base=get_env("DARAZ_API_BASE", DEFAULT_API_BASE),
    )


def cmd_list_stores(_: argparse.Namespace) -> int:
    stores = list_sanitized_stores()
    if not stores:
        print("No connected stores.")
        return 0
    print(json.dumps(stores, indent=2))
    return 0


def cmd_refresh_tokens(args: argparse.Namespace) -> int:
    results = refresh_store_tokens(
        store_id=args.store,
        force=args.force,
        within_minutes=args.within_minutes,
    )
    print(json.dumps(results, indent=2))
    return 0 if all(r.get("status") != "error" for r in results) else 1


def cmd_fetch_orders(args: argparse.Namespace) -> int:
    stores = _resolve_stores(args.store)
    created_after = args.created_after or _default_created_after()
    all_rows: list[dict[str, Any]] = []

    for store in stores:
        sid = store.get("store_id", "")
        client = _client_for_store(store)
        try:
            resp = client.get_orders(
                created_after=created_after,
                status=args.status,
                limit=args.limit,
                offset=0,
            )
            orders = _cap_orders(extract_orders(resp), args.limit)
            for order in orders:
                row = order_preview(order)
                row["store_id"] = sid
                row["store_name"] = store.get("store_name", "")
                all_rows.append(row)
            print(
                f"[{sid}] status={args.status} count={len(orders)} "
                f"created_after={created_after}",
                file=sys.stderr,
            )
        except DarazApiError as exc:
            print(f"[{sid}] GetOrders failed: [{exc.code}] {exc}", file=sys.stderr)
            return 1

    print(json.dumps(all_rows, indent=2))
    return 0


def _save_label_bytes(
    store_id: str,
    order_id: str,
    order_item_id: str,
    document: dict[str, Any],
) -> Path:
    mime = (document.get("mime_type") or document.get("MimeType") or "").lower()
    content = DarazClient.decode_document_file(document)
    ext = DarazClient.extension_for_mime(mime, content)
    out_dir = LABELS_DIR / store_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{order_id}__{order_item_id}{ext}"
    path.write_bytes(content)
    return path


def _fetch_labels_for_store(
    store: dict[str, Any],
    *,
    created_after: str,
    status: str,
    limit: int,
) -> list[Path]:
    client = _client_for_store(store)
    sid = str(store.get("store_id", "store"))
    sname = str(store.get("store_name", sid))
    resp = client.get_orders(
        created_after=created_after,
        status=status,
        limit=limit,
        offset=0,
    )
    orders = _cap_orders(extract_orders(resp), limit)
    saved: list[Path] = []

    for order in orders:
        order_id = order.get("order_id")
        if not order_id:
            continue
        items_resp = client.get_order_items(order_id)
        item_ids = eligible_item_ids(extract_order_items(items_resp))
        if not item_ids:
            continue
        doc_resp = client.get_shipping_label(item_ids)
        document = (doc_resp.get("data") or {}).get("document") or {}
        # One file per GetDocument call; name with first item id.
        path = _save_label_bytes(sid, str(order_id), item_ids[0], document)
        saved.append(path)
        print(f"[{sname}] saved {path}", file=sys.stderr)
    return saved


def cmd_fetch_labels(args: argparse.Namespace) -> int:
    stores = _resolve_stores(args.store)
    created_after = args.created_after or _default_created_after()
    saved: list[str] = []
    for store in stores:
        try:
            paths = _fetch_labels_for_store(
                store,
                created_after=created_after,
                status=args.status,
                limit=args.limit,
            )
            saved.extend(str(p) for p in paths)
        except DarazApiError as exc:
            print(
                f"[{store.get('store_id')}] label fetch failed: [{exc.code}] {exc}",
                file=sys.stderr,
            )
            return 1
    print(json.dumps({"saved": saved, "count": len(saved)}, indent=2))
    return 0


def _labels_from_disk(store_id: str | None) -> list[LabelDocument]:
    if not LABELS_DIR.exists():
        return []
    labels: list[LabelDocument] = []
    store_dirs = (
        [LABELS_DIR / store_id]
        if store_id
        else [p for p in LABELS_DIR.iterdir() if p.is_dir()]
    )
    store_lookup = {s.get("store_id"): s for s in list_stores()}
    for store_dir in store_dirs:
        if not store_dir.is_dir():
            continue
        sid = store_dir.name
        sname = str((store_lookup.get(sid) or {}).get("store_name") or sid)
        for path in sorted(store_dir.iterdir()):
            if path.suffix.lower() not in {".pdf", ".html"}:
                continue
            labels.append(load_label_from_file(path, store_id=sid, store_name=sname))
    return labels


def cmd_print_all(args: argparse.Namespace) -> int:
    stores = _resolve_stores(args.store)
    created_after = args.created_after or _default_created_after()
    labels: list[LabelDocument] = []

    if args.reuse_saved:
        labels = _labels_from_disk(args.store)[: args.limit]
    else:
        for store in stores:
            sid = str(store.get("store_id", "store"))
            sname = str(store.get("store_name", sid))
            client = _client_for_store(store)
            try:
                resp = client.get_orders(
                    created_after=created_after,
                    status=args.status,
                    limit=args.limit,
                    offset=0,
                )
                for order in _cap_orders(extract_orders(resp), args.limit):
                    order_id = order.get("order_id")
                    if not order_id:
                        continue
                    items_resp = client.get_order_items(order_id)
                    item_ids = eligible_item_ids(extract_order_items(items_resp))
                    if not item_ids:
                        continue
                    doc_resp = client.get_shipping_label(item_ids)
                    document = (doc_resp.get("data") or {}).get("document") or {}
                    _save_label_bytes(sid, str(order_id), item_ids[0], document)
                    labels.append(
                        document_from_daraz_response(
                            doc_resp,
                            store_id=sid,
                            store_name=sname,
                            order_id=str(order_id),
                            order_item_ids=item_ids,
                        )
                    )
            except DarazApiError as exc:
                print(f"[{sid}] print-all failed: [{exc.code}] {exc}", file=sys.stderr)
                return 1

    if not labels:
        # Fall back to any on-disk labels for the selected store(s).
        labels = _labels_from_disk(args.store)[: args.limit]

    if not labels:
        print("No labels to merge.", file=sys.stderr)
        return 1

    out = Path(args.output) if args.output else OUTPUT_DIR / "combined-labels.pdf"
    merge_labels(labels, out)
    pages = pdf_page_count(out)
    print(
        json.dumps(
            {
                "output": str(out),
                "labels": len(labels),
                "pages": pages,
            },
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Daraz multi-store Phase 3 CLI (read-only + GetDocument)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-stores", help="List connected stores (no tokens)")
    p_list.set_defaults(func=cmd_list_stores)

    p_ref = sub.add_parser("refresh-tokens", help="Refresh access tokens")
    p_ref.add_argument("--store", help="Store id or account")
    p_ref.add_argument("--force", action="store_true", help="Refresh even if not expiring")
    p_ref.add_argument(
        "--within-minutes",
        type=int,
        default=60,
        help="Refresh if access token expires within N minutes (default 60)",
    )
    p_ref.set_defaults(func=cmd_refresh_tokens)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", help="Limit to one store id or account")
    common.add_argument("--limit", type=int, default=10, help="Max orders per store")
    common.add_argument("--status", default="ready_to_ship")
    common.add_argument(
        "--created-after",
        help="ISO8601 lower bound (default: ~30 days ago)",
    )

    p_ord = sub.add_parser("fetch-orders", parents=[common], help="List orders")
    p_ord.set_defaults(func=cmd_fetch_orders)

    p_lab = sub.add_parser("fetch-labels", parents=[common], help="Download shipping labels")
    p_lab.set_defaults(func=cmd_fetch_labels)

    p_print = sub.add_parser(
        "print-all",
        parents=[common],
        help="Fetch labels and merge into one PDF",
    )
    p_print.add_argument(
        "--reuse-saved",
        action="store_true",
        help="Merge from data/labels instead of calling Daraz",
    )
    p_print.add_argument(
        "--output",
        help="Output PDF path (default data/output/combined-labels.pdf)",
    )
    p_print.set_defaults(func=cmd_print_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
