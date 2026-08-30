"""Shared store operations for CLI and web UI (read-only + GetDocument)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from src.config import DEFAULT_API_BASE, PROJECT_ROOT, get_env, require_env
from src.daraz_api import DarazApiError, DarazClient
from src.label_adapter import document_from_daraz_response, document_from_print_awb_response
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
    order_label_meta,
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


def _print_fetch_workers() -> int:
    raw = get_env("PRINT_FETCH_WORKERS", "8")
    try:
        return max(1, min(int(raw), 16))
    except ValueError:
        return 8


def _save_label_artifacts() -> bool:
    return get_env("SAVE_LABEL_ARTIFACTS", "").lower() in {"1", "true", "yes"}


LABEL_SOURCE_DISPLAY = {
    "print_awb_pdf": "Daraz PDF (PrintAWB)",
    "get_document_pdf": "Daraz PDF (GetDocument)",
    "get_document_html": "Daraz HTML",
    "saved_pdf": "Saved PDF",
    "saved_html": "Saved HTML",
}


def _disk_fetch_source(label: LabelDocument) -> str:
    return "saved_pdf" if label.is_pdf() else "saved_html"


def _label_detail(
    label: LabelDocument,
    fetch_source: str,
    *,
    converted: bool,
) -> dict[str, Any]:
    if converted:
        display = "Daraz HTML → converted"
        kind = "converted"
    else:
        display = LABEL_SOURCE_DISPLAY.get(fetch_source, "Daraz PDF")
        kind = "pdf"
    return {
        "order_id": label.order_id,
        "store_name": label.store_name,
        "mime_type": label.normalized_mime_type(),
        "fetch_source": fetch_source,
        "converted": converted,
        "display": display,
        "kind": kind,
    }


def _fetch_label_document(
    client: DarazClient,
    *,
    store_id: str,
    store_name: str,
    order_id: str,
    item_ids: list[str],
    package_id: str | None = None,
) -> tuple[LabelDocument, str]:
    """Prefer Daraz native PDF (PrintAWB) when package_id is known; else GetDocument."""
    if package_id:
        try:
            doc_resp = client.get_package_shipping_label(package_id, doc_type="PDF")
            label = document_from_print_awb_response(
                doc_resp,
                store_id=store_id,
                store_name=store_name,
                order_id=order_id,
                order_item_ids=item_ids,
            )
            if label.is_pdf():
                if _save_label_artifacts():
                    document = (doc_resp.get("data") or {})
                    save_label_bytes(store_id, order_id, item_ids[0], document)
                return label, "print_awb_pdf"
        except DarazApiError:
            pass

    doc_resp = client.get_shipping_label(item_ids)
    document = (doc_resp.get("data") or {}).get("document") or {}
    if _save_label_artifacts():
        save_label_bytes(store_id, order_id, item_ids[0], document)
    label = document_from_daraz_response(
        doc_resp,
        store_id=store_id,
        store_name=store_name,
        order_id=order_id,
        order_item_ids=item_ids,
    )
    fetch_source = "get_document_pdf" if label.is_pdf() else "get_document_html"
    return label, fetch_source


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
) -> dict[str, dict[str, Any]]:
    items_by_order: dict[str, dict[str, Any]] = {
        str(oid): {"item_ids": [], "package_id": None} for oid in order_ids
    }
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
                item_ids, package_id = order_label_meta(entries)
                if item_ids:
                    items_by_order[oid] = {
                        "item_ids": item_ids,
                        "package_id": package_id,
                    }

        if not parsed:
            for oid in chunk:
                items_resp = client.get_order_items(oid)
                items = extract_order_items(items_resp)
                item_ids, package_id = order_label_meta(items)
                items_by_order[str(oid)] = {
                    "item_ids": item_ids,
                    "package_id": package_id,
                }
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
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    Fetch shipping labels, convert each HTML label to PDF, then merge into one PDF.

    One GetDocument per order keeps HTML small so browser conversion is reliable.
    Limit = max orders to include.
    """
    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    created = created_after or default_created_after()
    raw_labels: list[LabelDocument] = []
    label_fetch_sources: list[str] = []
    collected_item_ids: list[str] = []
    limit = max(1, min(int(limit), 30))

    progress(f"Fetching up to {limit} orders…")

    if reuse_saved:
        raw_labels = labels_from_disk(store_id)[:limit]
        label_fetch_sources = [_disk_fetch_source(label) for label in raw_labels]
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
            work: list[tuple[str, list[str], str | None]] = []
            for oid in order_ids:
                meta = items_by_order.get(str(oid)) or {}
                item_ids = meta.get("item_ids") or []
                package_id = meta.get("package_id")
                if item_ids:
                    work.append((str(oid), item_ids, package_id))

            if work:
                progress(f"Downloading {len(work)} label(s) in parallel…")
                labels_by_order: dict[str, tuple[LabelDocument, str]] = {}
                workers = min(_print_fetch_workers(), len(work))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(
                            _fetch_label_document,
                            client,
                            store_id=sid,
                            store_name=sname,
                            order_id=oid,
                            item_ids=item_ids,
                            package_id=package_id,
                        ): oid
                        for oid, item_ids, package_id in work
                    }
                    done = 0
                    for future in as_completed(futures):
                        oid = futures[future]
                        labels_by_order[oid] = future.result()
                        done += 1
                        progress(f"Downloaded {done}/{len(work)} labels…")

                for oid, item_ids, _package_id in work:
                    label, fetch_source = labels_by_order[oid]
                    raw_labels.append(label)
                    label_fetch_sources.append(fetch_source)
                    collected_item_ids.extend(item_ids)

    if not raw_labels:
        raw_labels = labels_from_disk(store_id)[:limit]
        label_fetch_sources = [_disk_fetch_source(label) for label in raw_labels]
    if not raw_labels:
        raise ValueError("No labels to merge.")

    html_count = sum(1 for label in raw_labels if not label.is_pdf())
    pdf_count = len(raw_labels) - html_count
    pdf_labels: list[LabelDocument] = []

    if html_count == 0:
        progress(f"Using {pdf_count} Daraz PDF label(s) — no conversion needed.")
        pdf_labels = list(raw_labels)
    else:
        if pdf_count:
            progress(
                f"Using {pdf_count} Daraz PDF label(s); "
                f"converting {html_count} HTML label(s) to PDF…"
            )
        else:
            progress(f"Converting {html_count} HTML label(s) to PDF…")
        with html_converter_session() as converter:
            html_done = 0
            for idx, label in enumerate(raw_labels, start=1):
                if label.is_pdf():
                    pdf_labels.append(label)
                    continue
                html_done += 1
                progress(f"Rendering HTML label {html_done}/{html_count}…")
                pdf_label = _ensure_pdf_document(label, converter=converter)
                pdf_labels.append(pdf_label)
                if _save_label_artifacts():
                    out_dir = LABELS_DIR / pdf_label.store_id
                    out_dir.mkdir(parents=True, exist_ok=True)
                    pdf_path = out_dir / f"{pdf_label.order_id}__{pdf_label.order_item_id}.pdf"
                    pdf_path.write_bytes(pdf_label.document_bytes)

    out_pdf = output or (OUTPUT_DIR / "combined-labels.pdf")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    progress("Merging PDF…")
    merge_labels(pdf_labels, out_pdf)
    rel = (
        str(out_pdf.relative_to(PROJECT_ROOT))
        if out_pdf.is_relative_to(PROJECT_ROOT)
        else str(out_pdf)
    )
    if len(label_fetch_sources) != len(raw_labels):
        label_fetch_sources = [_disk_fetch_source(label) for label in raw_labels]
    label_details = [
        _label_detail(label, fetch_source, converted=not label.is_pdf())
        for label, fetch_source in zip(raw_labels, label_fetch_sources, strict=True)
    ]
    pdf_native = sum(1 for d in label_details if not d["converted"])
    html_converted = sum(1 for d in label_details if d["converted"])
    return {
        "output": str(out_pdf),
        "output_relative": rel.replace("\\", "/"),
        "download_url": "/api/download/combined-labels",
        "html_url": None,
        "format": "pdf",
        "labels": len(pdf_labels),
        "pages": pdf_page_count(out_pdf),
        "order_item_count": len(collected_item_ids) or len(pdf_labels),
        "label_details": label_details,
        "label_summary": {
            "pdf_native": pdf_native,
            "html_converted": html_converted,
        },
    }
