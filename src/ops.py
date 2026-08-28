"""Shared store operations for CLI and web UI (read-only + GetDocument)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import DEFAULT_API_BASE, PROJECT_ROOT, get_env, require_env
from src.daraz_api import DarazClient
from src.label_adapter import document_from_daraz_response
from src.label_processor import (
    LABELS_DIR,
    OUTPUT_DIR,
    LabelDocument,
    get_html_converter,
    html_converter_session,
    load_label_from_file,
    merge_labels,
    pdf_page_count,
)
from src.orders import (
    eligible_item_ids,
    extract_order_items,
    extract_orders,
    is_label_eligible,
    order_preview,
)
from src.token_store import get_store, list_stores


def default_created_after(days: int = 30) -> str:
    dt = datetime.now(UTC) - timedelta(days=days)
    return dt.astimezone().replace(microsecond=0).isoformat()


def cap_orders(orders: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return orders
    return orders[:limit]


def resolve_stores(store_id: str | None = None) -> list[dict[str, Any]]:
    if store_id:
        store = get_store(store_id)
        if not store:
            raise ValueError(f"Unknown store: {store_id}")
        return [store]
    stores = list_stores()
    if not stores:
        raise ValueError("No stores connected. Connect a seller via OAuth first.")
    return stores


def client_for_store(store: dict[str, Any]) -> DarazClient:
    token = store.get("access_token")
    if not token:
        raise ValueError(f"Store {store.get('store_id')} has no access_token")
    return DarazClient(
        app_key=require_env("DARAZ_APP_KEY"),
        app_secret=require_env("DARAZ_APP_SECRET"),
        access_token=str(token),
        api_base=get_env("DARAZ_API_BASE", DEFAULT_API_BASE),
    )


def save_label_bytes(
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


def fetch_orders(
    *,
    store_id: str | None = None,
    status: str = "ready_to_ship",
    limit: int = 10,
    created_after: str | None = None,
) -> list[dict[str, Any]]:
    created = created_after or default_created_after()
    rows: list[dict[str, Any]] = []
    for store in resolve_stores(store_id):
        sid = store.get("store_id", "")
        client = client_for_store(store)
        resp = client.get_orders(
            created_after=created,
            status=status,
            limit=limit,
            offset=0,
        )
        for order in cap_orders(extract_orders(resp), limit):
            row = order_preview(order)
            row["store_id"] = sid
            row["store_name"] = store.get("store_name", "")
            rows.append(row)
    return rows


def fetch_labels(
    *,
    store_id: str | None = None,
    status: str = "ready_to_ship",
    limit: int = 10,
    created_after: str | None = None,
) -> list[str]:
    created = created_after or default_created_after()
    saved: list[str] = []
    for store in resolve_stores(store_id):
        sid = str(store.get("store_id", "store"))
        client = client_for_store(store)
        resp = client.get_orders(
            created_after=created,
            status=status,
            limit=limit,
            offset=0,
        )
        for order in cap_orders(extract_orders(resp), limit):
            order_id = order.get("order_id")
            if not order_id:
                continue
            items_resp = client.get_order_items(order_id)
            item_ids = eligible_item_ids(extract_order_items(items_resp))
            if not item_ids:
                continue
            doc_resp = client.get_shipping_label(item_ids)
            document = (doc_resp.get("data") or {}).get("document") or {}
            path = save_label_bytes(sid, str(order_id), item_ids[0], document)
            saved.append(str(path))
    return saved


def labels_from_disk(store_id: str | None = None) -> list[LabelDocument]:
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


def _items_by_order(
    client: DarazClient,
    order_ids: list[Any],
) -> dict[str, list[str]]:
    items_by_order: dict[str, list[str]] = {str(oid): [] for oid in order_ids}
    for i in range(0, len(order_ids), 50):
        chunk = order_ids[i : i + 50]
        try:
            multi = client.get_multiple_order_items(chunk)
            data = multi.get("data")
        except Exception:
            data = None

        parsed = False
        if isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                oid = str(entry.get("order_id") or "")
                entries = (
                    entry.get("order_items")
                    or entry.get("orderItems")
                    or entry.get("items")
                    or []
                )
                if not isinstance(entries, list):
                    continue
                parsed = True
                for item in entries:
                    if not isinstance(item, dict) or not is_label_eligible(item):
                        continue
                    iid = item.get("order_item_id")
                    if not iid:
                        continue
                    items_by_order.setdefault(oid, []).append(str(iid))

        if not parsed:
            for oid in chunk:
                items_resp = client.get_order_items(oid)
                items_by_order[str(oid)] = eligible_item_ids(
                    extract_order_items(items_resp)
                )
    return items_by_order


def _ensure_pdf_document(
    label: LabelDocument,
    *,
    converter: Any | None = None,
) -> LabelDocument:
    """Convert HTML labels to PDF bytes; pass through real PDFs unchanged."""
    if label.is_pdf():
        return label
    conv = converter or get_html_converter()
    pdf_bytes = label.as_pdf_bytes(html_converter=conv)
    base = label.source_filename.rsplit(".", 1)[0]
    return LabelDocument(
        store_id=label.store_id,
        store_name=label.store_name,
        order_id=label.order_id,
        order_item_id=label.order_item_id,
        source_filename=f"{base}.pdf",
        mime_type="application/pdf",
        document_bytes=pdf_bytes,
    )


def print_labels(
    *,
    store_id: str | None = None,
    status: str = "ready_to_ship",
    limit: int = 5,
    created_after: str | None = None,
    reuse_saved: bool = False,
    output: Path | None = None,
) -> dict[str, Any]:
    """
    Fetch shipping labels, convert each HTML label to PDF, then merge into one PDF.

    One GetDocument per order keeps HTML small so browser conversion is reliable.
    Limit = max orders to include.
    """
    created = created_after or default_created_after()
    raw_labels: list[LabelDocument] = []
    collected_item_ids: list[str] = []
    limit = max(1, min(int(limit), 30))

    if reuse_saved:
        raw_labels = labels_from_disk(store_id)[:limit]
    else:
        for store in resolve_stores(store_id):
            sid = str(store.get("store_id", "store"))
            sname = str(store.get("store_name", sid))
            client = client_for_store(store)
            resp = client.get_orders(
                created_after=created,
                status=status,
                limit=limit,
                offset=0,
            )
            orders = cap_orders(extract_orders(resp), limit)
            order_ids = [o.get("order_id") for o in orders if o.get("order_id")]
            items_by_order = _items_by_order(client, order_ids)

            for oid in order_ids:
                item_ids = items_by_order.get(str(oid)) or []
                if not item_ids:
                    continue
                collected_item_ids.extend(item_ids)
                doc_resp = client.get_shipping_label(item_ids)
                document = (doc_resp.get("data") or {}).get("document") or {}
                save_label_bytes(sid, str(oid), item_ids[0], document)
                raw_labels.append(
                    document_from_daraz_response(
                        doc_resp,
                        store_id=sid,
                        store_name=sname,
                        order_id=str(oid),
                        order_item_ids=item_ids,
                    )
                )

    if not raw_labels:
        raw_labels = labels_from_disk(store_id)[:limit]
    if not raw_labels:
        raise ValueError("No labels to merge.")

    with html_converter_session() as converter:
        pdf_labels: list[LabelDocument] = []
        for label in raw_labels:
            pdf_label = _ensure_pdf_document(label, converter=converter)
            pdf_labels.append(pdf_label)
            out_dir = LABELS_DIR / pdf_label.store_id
            out_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = out_dir / f"{pdf_label.order_id}__{pdf_label.order_item_id}.pdf"
            pdf_path.write_bytes(pdf_label.document_bytes)

    out_pdf = output or (OUTPUT_DIR / "combined-labels.pdf")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    merge_labels(pdf_labels, out_pdf)
    rel = (
        str(out_pdf.relative_to(PROJECT_ROOT))
        if out_pdf.is_relative_to(PROJECT_ROOT)
        else str(out_pdf)
    )
    return {
        "output": str(out_pdf),
        "output_relative": rel.replace("\\", "/"),
        "download_url": "/api/download/combined-labels",
        "html_url": None,
        "format": "pdf",
        "labels": len(pdf_labels),
        "pages": pdf_page_count(out_pdf),
        "order_item_count": len(collected_item_ids) or len(pdf_labels),
    }
