"""Shared store operations for CLI and web UI (read-only + GetDocument)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from math import ceil, log2
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
from src.store_display import store_display_name
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


def _env_flag(name: str, default: str) -> bool:
    return get_env(name, default).lower() not in {"0", "false", "no"}


def _prefer_native_pdf() -> bool:
    """Default ON — prefer PrintAWB native PDF over bulk-first GetDocument."""
    return _env_flag("PRINT_PREFER_NATIVE_PDF", "1")


def _prefer_bulk_getdocument() -> bool:
    """Bulk-first GetDocument. Default OFF; ignored when native PDF is preferred."""
    if _prefer_native_pdf():
        return False
    return _env_flag("PRINT_PREFER_BULK_GETDOCUMENT", "0")


def _label_pdf_page_count(label: LabelDocument) -> int:
    """Page count for a PDF LabelDocument (0 if not PDF). Does not convert HTML."""
    if not label.is_pdf():
        return 0
    try:
        return int(label.page_count())
    except Exception:  # noqa: BLE001
        return 0


def _bulk_pages_match_targets(
    pages: int, work: list[tuple[dict[str, Any], list[str], str | None]]
) -> bool:
    """True when PDF pages map 1:1 to orders or 1:1 to item ids."""
    order_count = len(work)
    item_count = sum(len(ids) for _t, ids, _pkg in work)
    return pages == order_count or pages == item_count


def _save_label_artifacts() -> bool:
    return get_env("SAVE_LABEL_ARTIFACTS", "").lower() in {"1", "true", "yes"}


LABEL_SOURCE_DISPLAY = {
    "print_awb_pdf": "Daraz PDF (PrintAWB)",
    "get_document_pdf": "Daraz PDF (GetDocument)",
    "get_document_html": "Daraz HTML",
    "get_document_bulk": "Daraz PDF (GetDocument bulk)",
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
    package_id: str | None = None,
    fetch_notes: str | None = None,
) -> dict[str, Any]:
    if converted:
        display = "Daraz HTML → converted"
        kind = "converted"
    else:
        display = LABEL_SOURCE_DISPLAY.get(fetch_source, "Daraz PDF")
        kind = "pdf"
    detail = {
        "order_id": label.order_id,
        "store_name": label.store_name,
        "mime_type": label.normalized_mime_type(),
        "fetch_source": fetch_source,
        "converted": converted,
        "display": display,
        "kind": kind,
        "package_id": package_id,
    }
    if fetch_notes:
        detail["fetch_notes"] = fetch_notes
    return detail


def _fetch_label_document(
    client: DarazClient,
    *,
    store_id: str,
    store_name: str,
    order_id: str,
    item_ids: list[str],
    package_id: str | None = None,
) -> tuple[LabelDocument, str, dict[str, Any]]:
    """Prefer Daraz native PDF (PrintAWB) when package_id is known; else GetDocument."""
    meta: dict[str, Any] = {
        "package_id": package_id,
        "print_awb_attempted": False,
        "print_awb_error": None,
    }

    if package_id:
        meta["print_awb_attempted"] = True
        try:
            doc_resp = client.get_package_shipping_label(package_id, doc_type="PDF")
            label = document_from_print_awb_response(
                doc_resp,
                store_id=store_id,
                store_name=store_name,
                order_id=order_id,
                order_item_ids=item_ids,
                download_url=client.download_binary_url,
            )
            if label.is_pdf():
                if _save_label_artifacts():
                    out_dir = LABELS_DIR / store_id
                    out_dir.mkdir(parents=True, exist_ok=True)
                    pdf_path = out_dir / f"{order_id}__{item_ids[0]}.pdf"
                    pdf_path.write_bytes(label.document_bytes)
                return label, "print_awb_pdf", meta
            meta["print_awb_error"] = "PrintAWB returned non-PDF content"
        except DarazApiError as exc:
            if exc.code == "InsufficientPermission":
                meta["print_awb_error"] = (
                    "PrintAWB not enabled for this app — enable "
                    "/order/package/document/get in Daraz App Console"
                )
            else:
                meta["print_awb_error"] = f"[{exc.code or '?'}] {exc}"
    else:
        meta["print_awb_error"] = "no package_id on order items"

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
    return label, fetch_source, meta


def resolve_stores(
    store_id: str | None = None,
    *,
    store_ids: list[str] | None = None,
    get_store_fn: Callable[[str], dict[str, Any] | None] | None = None,
    list_stores_fn: Callable[[], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    get_one = get_store_fn or get_store
    list_all = list_stores_fn or list_stores
    if store_ids is not None:
        if not store_ids:
            raise ValueError("No stores selected. Pick at least one store.")
        stores: list[dict[str, Any]] = []
        for sid in store_ids:
            store = get_one(sid)
            if not store:
                raise ValueError(f"Unknown store: {sid}")
            stores.append(store)
        return stores
    if store_id:
        store = get_one(store_id)
        if not store:
            raise ValueError(f"Unknown store: {store_id}")
        return [store]
    stores = list_all()
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
    store_ids: list[str] | None = None,
    status: str = "ready_to_ship",
    limit: int = 10,
    created_after: str | None = None,
    get_store_fn: Callable[[str], dict[str, Any] | None] | None = None,
    list_stores_fn: Callable[[], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    created = created_after or default_created_after()
    rows: list[dict[str, Any]] = []
    for store in resolve_stores(
        store_id,
        store_ids=store_ids,
        get_store_fn=get_store_fn,
        list_stores_fn=list_stores_fn,
    ):
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
            row["store_name"] = store_display_name(store)
            rows.append(row)
    return rows


def fetch_labels(
    *,
    store_id: str | None = None,
    store_ids: list[str] | None = None,
    status: str = "ready_to_ship",
    limit: int = 10,
    created_after: str | None = None,
) -> list[str]:
    created = created_after or default_created_after()
    saved: list[str] = []
    for store in resolve_stores(store_id, store_ids=store_ids):
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


def labels_from_disk(
    store_id: str | None = None,
    *,
    store_ids: list[str] | None = None,
) -> list[LabelDocument]:
    if store_ids:
        merged: list[LabelDocument] = []
        for sid in store_ids:
            merged.extend(labels_from_disk(sid))
        return merged
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
        sname = store_display_name(store_lookup.get(sid) or {"store_id": sid})
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
    store_ids: list[str] | None = None,
    status: str = "ready_to_ship",
    limit: int = 5,
    created_after: str | None = None,
    reuse_saved: bool = False,
    output: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
    get_store_fn: Callable[[str], dict[str, Any] | None] | None = None,
    list_stores_fn: Callable[[], list[dict[str, Any]]] | None = None,
    download_url: str | None = None,
    allow_reprint: bool = False,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """
    Fetch shipping labels, convert each HTML label to PDF, then merge into one PDF.

    One GetDocument per order keeps HTML small so browser conversion is reliable.
    Limit = max orders to include.

    When ``allow_reprint`` is False and ``workspace_id`` is set, orders that already
    have an ``order_label_prints`` row for ``(store_uuid, daraz_order_id)`` are skipped.
    """
    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    created = created_after or default_created_after()
    raw_labels: list[LabelDocument] = []
    label_fetch_sources: list[str] = []
    label_fetch_meta: list[dict[str, Any]] = []
    collected_item_ids: list[str] = []
    limit = max(1, min(int(limit), 30))

    progress(f"Fetching up to {limit} orders…")

    if reuse_saved:
        raw_labels = labels_from_disk(store_id, store_ids=store_ids)[:limit]
        label_fetch_sources = [_disk_fetch_source(label) for label in raw_labels]
        label_fetch_meta = [{} for _ in raw_labels]
    else:
        from src.db.repo import get_repo

        repo = get_repo() if workspace_id else None
        for store in resolve_stores(
            store_id,
            store_ids=store_ids,
            get_store_fn=get_store_fn,
            list_stores_fn=list_stores_fn,
        ):
            sid = str(store.get("store_id", "store"))
            store_uuid = str(store.get("id") or "")
            sname = store_display_name(store)
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
                if not item_ids:
                    continue
                if (
                    not allow_reprint
                    and repo
                    and workspace_id
                    and store_uuid
                    and repo.has_label_print(workspace_id, store_uuid, str(oid))
                ):
                    continue
                work.append((str(oid), item_ids, package_id))

            if work:
                progress(f"Downloading {len(work)} label(s) in parallel…")
                labels_by_order: dict[str, tuple[LabelDocument, str, dict[str, Any]]] = {}
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
                    label, fetch_source, fetch_meta = labels_by_order[oid]
                    raw_labels.append(label)
                    label_fetch_sources.append(fetch_source)
                    label_fetch_meta.append(fetch_meta)
                    collected_item_ids.extend(item_ids)

    if not raw_labels:
        raw_labels = labels_from_disk(store_id, store_ids=store_ids)[:limit]
        label_fetch_sources = [_disk_fetch_source(label) for label in raw_labels]
        label_fetch_meta = [{} for _ in raw_labels]
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
                progress(f"Converting Daraz HTML to PDF ({html_done}/{html_count})…")
                pdf_label = _ensure_pdf_document(label, converter=converter)
                pdf_labels.append(pdf_label)
                if _save_label_artifacts():
                    out_dir = LABELS_DIR / pdf_label.store_id
                    out_dir.mkdir(parents=True, exist_ok=True)
                    pdf_path = out_dir / f"{pdf_label.order_id}__{pdf_label.order_item_id}.pdf"
                    pdf_path.write_bytes(pdf_label.document_bytes)

    out_pdf = output or (OUTPUT_DIR / "combined-labels.pdf")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    progress("Merging PDF…")
    merge_labels(pdf_labels, out_pdf)
    rel = (
        str(out_pdf.relative_to(PROJECT_ROOT))
        if out_pdf.is_relative_to(PROJECT_ROOT)
        else str(out_pdf)
    )
    if len(label_fetch_sources) != len(raw_labels):
        label_fetch_sources = [_disk_fetch_source(label) for label in raw_labels]
        label_fetch_meta = [{} for _ in raw_labels]
    label_details = []
    for label, fetch_source, fetch_meta in zip(
        raw_labels, label_fetch_sources, label_fetch_meta, strict=True
    ):
        notes = fetch_meta.get("print_awb_error")
        if fetch_meta.get("print_awb_attempted") and fetch_source == "print_awb_pdf":
            notes = None
        label_details.append(
            _label_detail(
                label,
                fetch_source,
                converted=not label.is_pdf(),
                package_id=fetch_meta.get("package_id"),
                fetch_notes=notes,
            )
        )
    pdf_native = sum(1 for d in label_details if not d["converted"])
    html_converted = sum(1 for d in label_details if d["converted"])
    return {
        "output": str(out_pdf),
        "output_relative": rel.replace("\\", "/"),
        "download_url": download_url or "/api/download/combined-labels",
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


def print_labels_for_orders(
    workspace_id: str,
    user_id: str,
    order_uuids: list[str],
    *,
    allow_reprint: bool = False,
    job_id: str | None = None,
    download_url: str | None = None,
    output: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Print labels for local DB order UUIDs (one document per order).

    Hydrates missing items, validates targets, and records an explicit outcome
    for every selected order (never silent-skip). Already-printed orders are
    blocked unless ``allow_reprint``. Print events are recorded only after a
    successful PDF merge for SUCCESS outcomes.
    """
    import time

    from src.db.repo import get_repo
    from src.print_hydrate import hydrate_missing_order_items
    from src.print_outcomes import (
        ALREADY_PRINTED,
        DARAZ_ERROR,
        DOCUMENT_FAILED,
        DOCUMENT_MAPPING_FAILED,
        MERGE_FAILED,
        NOT_ELIGIBLE,
        NOT_FOUND,
        PACKAGE_RESOLUTION_FAILED,
        STORE_NOT_FOUND,
        SUCCESS,
        UNKNOWN_FAILURE,
        make_outcome,
        summarize_outcomes,
    )
    from src.print_safety import (
        order_is_eligible,
        record_label_prints,
        validate_print_targets,
    )

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    t_total = time.perf_counter()
    # Update UI immediately — hydrate/Daraz work must not leave "Starting print job…"
    progress("Gathering labels…")
    repo = get_repo()

    # Preserve selection order; drop duplicates
    selected: list[str] = []
    seen: set[str] = set()
    for raw_id in order_uuids:
        oid = str(raw_id)
        if oid in seen:
            continue
        seen.add(oid)
        selected.append(oid)

    hydrate = hydrate_missing_order_items(workspace_id, selected)
    hydrate_ms = int(hydrate.get("hydrate_ms") or 0)

    t_val = time.perf_counter()
    validated = validate_print_targets(workspace_id, selected)
    validation_ms = int((time.perf_counter() - t_val) * 1000)

    store_cache: dict[str, dict[str, Any] | None] = {}

    def _store_meta(store_uuid: str | None) -> dict[str, Any]:
        if not store_uuid:
            return {
                "store_id": None,
                "store_slug": None,
                "store_display_name": None,
            }
        if store_uuid not in store_cache:
            store_cache[store_uuid] = repo.get_store_by_uuid(workspace_id, store_uuid)
        store = store_cache[store_uuid]
        if not store:
            return {
                "store_id": store_uuid,
                "store_slug": None,
                "store_display_name": None,
            }
        return {
            "store_id": store_uuid,
            "store_slug": str(store.get("store_id") or ""),
            "store_display_name": store_display_name(store),
        }

    def _order_fields(oid: str, entry: dict[str, Any] | None = None) -> dict[str, Any]:
        order = repo.get_order_by_id(workspace_id, oid)
        store_uuid = str(
            (entry or {}).get("store_id")
            or (order or {}).get("store_id")
            or ""
        ) or None
        meta = _store_meta(store_uuid)
        return {
            "daraz_order_id": str(
                (entry or {}).get("daraz_order_id")
                or (order or {}).get("daraz_order_id")
                or ""
            )
            or None,
            "order_number": str((order or {}).get("order_number") or "") or None,
            **meta,
            "package_id": (entry or {}).get("package_id"),
            "order_item_ids": list((entry or {}).get("order_item_ids") or []),
        }

    outcomes_by_id: dict[str, dict[str, Any]] = {}
    fetch_targets: list[dict[str, Any]] = []
    reprint_ids: set[str] = set()

    for err in validated["errors"]:
        oid = str(err["order_id"])
        err_code = str(err.get("error") or "not_found")
        state = STORE_NOT_FOUND if err_code == "store_not_found" else NOT_FOUND
        outcomes_by_id[oid] = make_outcome(
            order_id=oid,
            state=state,
            reason=err_code,
            **_order_fields(oid),
        )

    for entry in validated["not_eligible"]:
        oid = str(entry["order_id"])
        order = repo.get_order_by_id(workspace_id, oid)
        items = repo.list_order_items(workspace_id, oid) if order else []
        item_ids = list(entry.get("order_item_ids") or [])
        if not item_ids:
            # Eligible header / missing lines after hydrate → package resolution
            if not items or (order and order_is_eligible(order, items)):
                state = PACKAGE_RESOLUTION_FAILED
                reason = "no_order_item_ids"
            else:
                state = NOT_ELIGIBLE
                reason = str(entry.get("reason") or "not_eligible")
        else:
            state = NOT_ELIGIBLE
            reason = str(entry.get("reason") or "not_eligible")
        outcomes_by_id[oid] = make_outcome(
            order_id=oid,
            state=state,
            reason=reason,
            **_order_fields(oid, entry),
        )

    for entry in validated["already_printed"]:
        oid = str(entry["order_id"])
        if allow_reprint:
            fetch_targets.append(entry)
            reprint_ids.add(oid)
        else:
            outcomes_by_id[oid] = make_outcome(
                order_id=oid,
                state=ALREADY_PRINTED,
                reason="already_printed",
                is_reprint=False,
                **_order_fields(oid, entry),
            )

    for entry in validated["new_printable"]:
        oid = str(entry["order_id"])
        if not entry.get("order_item_ids"):
            outcomes_by_id[oid] = make_outcome(
                order_id=oid,
                state=PACKAGE_RESOLUTION_FAILED,
                reason="no_order_item_ids",
                **_order_fields(oid, entry),
            )
        else:
            fetch_targets.append(entry)

    by_store: dict[str, list[dict[str, Any]]] = {}
    for t in fetch_targets:
        by_store.setdefault(str(t["store_id"]), []).append(t)

    raw_labels: list[LabelDocument] = []
    label_fetch_sources: list[str] = []
    label_fetch_meta: list[dict[str, Any]] = []
    # Each pending success maps to a label index in raw_labels (bulk shares one index).
    pending_successes: list[dict[str, Any]] = []
    document_diagnostics: list[dict[str, Any]] = []

    printawb_calls = 0
    getdocument_calls = 0
    bulk_calls = 0
    printawb_ms = 0
    bulk_getdocument_ms = 0
    fallback_fetch_ms = 0
    native_pdf_docs = 0
    html_docs_converted = 0

    use_native = _prefer_native_pdf()
    use_bulk_first = _prefer_bulk_getdocument()  # False when native preferred
    use_phase2_bulk = use_native or use_bulk_first

    def _append_success(
        t: dict[str, Any],
        item_ids: list[str],
        package_id: str | None,
        *,
        label: LabelDocument,
        fetch_source: str,
        fetch_meta: dict[str, Any],
        label_index: int | None = None,
        expected_pages: int = 1,
    ) -> None:
        nonlocal native_pdf_docs
        if label_index is None:
            label_index = len(raw_labels)
            raw_labels.append(label)
            label_fetch_sources.append(fetch_source)
            label_fetch_meta.append(fetch_meta)
            if label.is_pdf() and fetch_source in {
                "print_awb_pdf",
                "get_document_pdf",
                "get_document_bulk",
            }:
                native_pdf_docs += 1
        oid = str(t["order_id"])
        pending_successes.append(
            {
                "order_id": oid,
                "store_id": store_uuid,
                "daraz_order_id": str(t["daraz_order_id"]),
                "order_item_ids": item_ids,
                "package_id": package_id,
                "is_reprint": oid in reprint_ids,
                "fetch_source": fetch_source,
                "entry": t,
                "fetch_meta": fetch_meta,
                "label_index": label_index,
                "expected_pages": expected_pages,
            }
        )

    def _fetch_print_awb_only(
        *,
        order_id: str,
        item_ids: list[str],
        package_id: str,
    ) -> tuple[LabelDocument, str, dict[str, Any]]:
        meta: dict[str, Any] = {
            "package_id": package_id,
            "print_awb_attempted": True,
            "print_awb_error": None,
        }
        doc_resp = client.get_package_shipping_label(package_id, doc_type="PDF")
        label = document_from_print_awb_response(
            doc_resp,
            store_id=sid,
            store_name=sname,
            order_id=order_id,
            order_item_ids=item_ids,
            download_url=client.download_binary_url,
        )
        if label.is_pdf():
            if _save_label_artifacts():
                out_dir = LABELS_DIR / sid
                out_dir.mkdir(parents=True, exist_ok=True)
                pdf_path = out_dir / f"{order_id}__{item_ids[0]}.pdf"
                pdf_path.write_bytes(label.document_bytes)
            return label, "print_awb_pdf", meta
        meta["print_awb_error"] = "PrintAWB returned non-PDF content"
        raise DarazApiError("PrintAWB returned non-PDF content", code="NOT_PDF")

    def _run_phase2_bulk(
        group: list[tuple[dict[str, Any], list[str], str | None]],
        *,
        depth: int,
        diag: dict[str, Any],
        max_depth: int,
    ) -> list[tuple[dict[str, Any], list[str], str | None]]:
        """Try bulk GetDocument; return targets that still need per-order fetch."""
        nonlocal getdocument_calls, bulk_calls, bulk_getdocument_ms

        if not group:
            return []

        all_ids: list[str] = []
        for _t, item_ids, _pkg in group:
            all_ids.extend(str(i) for i in item_ids)

        diag["bulk_attempted"] = True
        progress(
            f"Gathering labels… bulk GetDocument "
            f"({len(group)} order(s), {len(all_ids)} item id(s))"
        )
        t_bulk = time.perf_counter()
        try:
            try:
                doc_resp = client.get_shipping_label(all_ids)
            finally:
                bulk_getdocument_ms += int((time.perf_counter() - t_bulk) * 1000)
                getdocument_calls += 1
                bulk_calls += 1

            if _save_label_artifacts():
                document = (doc_resp.get("data") or {}).get("document") or {}
                save_label_bytes(sid, "bulk", all_ids[0], document)

            bulk_label = document_from_daraz_response(
                doc_resp,
                store_id=sid,
                store_name=sname,
                order_id="bulk",
                order_item_ids=all_ids,
            )

            if bulk_label.is_pdf():
                pages_n = _label_pdf_page_count(bulk_label)
                diag["bulk_format"] = "pdf"
                diag["pages"] = pages_n
                if _bulk_pages_match_targets(pages_n, group):
                    diag["bulk_success"] = True
                    fetch_source = "get_document_bulk"
                    fetch_meta: dict[str, Any] = {
                        "package_id": None,
                        "print_awb_attempted": False,
                        "print_awb_error": None,
                        "fetch_source": fetch_source,
                        "api_calls": 1,
                        "item_ids": all_ids,
                        "order_count": len(group),
                        "pages": pages_n,
                    }
                    label_index = len(raw_labels)
                    raw_labels.append(bulk_label)
                    label_fetch_sources.append(fetch_source)
                    label_fetch_meta.append(fetch_meta)
                    nonlocal native_pdf_docs
                    native_pdf_docs += 1
                    # Map 1 page ≈ 1 order when counts match on order_count;
                    # when match is on item_count, still one success per order.
                    for t, item_ids, package_id in group:
                        _append_success(
                            t,
                            item_ids,
                            package_id,
                            label=bulk_label,
                            fetch_source=fetch_source,
                            fetch_meta={
                                **fetch_meta,
                                "item_ids": list(item_ids),
                                "package_id": package_id,
                            },
                            label_index=label_index,
                            expected_pages=1,
                        )
                    return []

                progress(
                    f"Bulk PDF page count {pages_n} ≠ {len(group)} order(s); "
                    f"isolating…"
                )
                diag["fallback_reason"] = (
                    diag.get("fallback_reason")
                    or f"page_mismatch:{pages_n}_vs_{len(group)}"
                )
                if len(group) <= 1 or depth >= max_depth:
                    return group
                mid = len(group) // 2
                left = _run_phase2_bulk(
                    group[:mid], depth=depth + 1, diag=diag, max_depth=max_depth
                )
                right = _run_phase2_bulk(
                    group[mid:], depth=depth + 1, diag=diag, max_depth=max_depth
                )
                return left + right

            # HTML (or unknown) — never treat multi-order HTML as SUCCESS blob
            fmt = "html" if bulk_label.is_html() else "unknown"
            diag["bulk_format"] = fmt
            diag["bulk_success"] = False
            diag["fallback_reason"] = diag.get("fallback_reason") or f"bulk_{fmt}"
            progress(
                f"Bulk GetDocument returned {fmt}; "
                f"falling back per-order for {len(group)} target(s)…"
            )
            return group

        except DarazApiError as exc:
            diag["bulk_success"] = False
            diag["fallback_reason"] = (
                diag.get("fallback_reason")
                or f"bulk_api_error:{exc.code or '?'}"
            )
            progress(
                f"Bulk GetDocument failed [{exc.code or '?'}]; isolating…"
            )
            if len(group) <= 1 or depth >= max_depth:
                return group
            mid = len(group) // 2
            left = _run_phase2_bulk(
                group[:mid], depth=depth + 1, diag=diag, max_depth=max_depth
            )
            right = _run_phase2_bulk(
                group[mid:], depth=depth + 1, diag=diag, max_depth=max_depth
            )
            return left + right
        except Exception as exc:  # noqa: BLE001
            diag["bulk_success"] = False
            diag["fallback_reason"] = (
                diag.get("fallback_reason") or f"bulk_error:{exc}"
            )
            progress(f"Bulk GetDocument failed ({exc}); isolating…")
            if len(group) <= 1 or depth >= max_depth:
                return group
            mid = len(group) // 2
            left = _run_phase2_bulk(
                group[:mid], depth=depth + 1, diag=diag, max_depth=max_depth
            )
            right = _run_phase2_bulk(
                group[mid:], depth=depth + 1, diag=diag, max_depth=max_depth
            )
            return left + right

    def _run_per_order_fallback(
        fallback_work: list[tuple[dict[str, Any], list[str], str | None]],
    ) -> None:
        nonlocal printawb_calls, getdocument_calls, fallback_fetch_ms, printawb_ms

        if not fallback_work:
            return

        labels_by_order: dict[str, tuple[LabelDocument, str, dict[str, Any]]] = {}
        workers = min(_print_fetch_workers(), len(fallback_work))
        progress(f"Gathering labels… {len(fallback_work)} order(s)")
        t_fb = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _fetch_label_document,
                    client,
                    store_id=sid,
                    store_name=sname,
                    order_id=str(t["daraz_order_id"]),
                    item_ids=item_ids,
                    package_id=package_id,
                ): (t, item_ids, package_id)
                for t, item_ids, package_id in fallback_work
            }
            done = 0
            for future in as_completed(futures):
                t, _item_ids, _package_id = futures[future]
                oid = str(t["order_id"])
                done += 1
                if done == 1 or done == len(fallback_work) or done % 5 == 0:
                    progress(f"Gathering labels… {done}/{len(fallback_work)}")
                try:
                    labels_by_order[oid] = future.result()
                except DarazApiError as exc:
                    outcomes_by_id[oid] = make_outcome(
                        order_id=oid,
                        state=DARAZ_ERROR,
                        reason=str(exc),
                        daraz_code=exc.code,
                        daraz_message=str(exc),
                        is_reprint=oid in reprint_ids,
                        **_order_fields(oid, t),
                    )
                except Exception as exc:  # noqa: BLE001
                    outcomes_by_id[oid] = make_outcome(
                        order_id=oid,
                        state=DOCUMENT_FAILED,
                        reason=str(exc),
                        is_reprint=oid in reprint_ids,
                        **_order_fields(oid, t),
                    )
        fallback_fetch_ms += int((time.perf_counter() - t_fb) * 1000)

        for t, item_ids, package_id in fallback_work:
            oid = str(t["order_id"])
            if oid not in labels_by_order:
                if oid not in outcomes_by_id:
                    outcomes_by_id[oid] = make_outcome(
                        order_id=oid,
                        state=DOCUMENT_FAILED,
                        reason="label_fetch_missing",
                        is_reprint=oid in reprint_ids,
                        **_order_fields(oid, t),
                    )
                continue
            label, fetch_source, fetch_meta = labels_by_order[oid]
            if fetch_meta.get("print_awb_attempted"):
                printawb_calls += 1
            if fetch_source != "print_awb_pdf":
                getdocument_calls += 1
            pages_n = _label_pdf_page_count(label) if label.is_pdf() else 1
            _append_success(
                t,
                item_ids,
                package_id or fetch_meta.get("package_id"),
                label=label,
                fetch_source=fetch_source,
                fetch_meta=fetch_meta,
                expected_pages=max(1, pages_n) if label.is_pdf() else 1,
            )

    t_fetch = time.perf_counter()
    if fetch_targets:
        progress(f"Gathering labels… {len(fetch_targets)} order(s)")

    for store_uuid, store_targets in by_store.items():
        store = repo.get_store_by_uuid(workspace_id, store_uuid)
        store_cache[store_uuid] = store
        if not store:
            for t in store_targets:
                oid = str(t["order_id"])
                outcomes_by_id[oid] = make_outcome(
                    order_id=oid,
                    state=STORE_NOT_FOUND,
                    reason="store_not_found",
                    **_order_fields(oid, t),
                )
            continue
        sid = str(store.get("store_id", "store"))
        sname = store_display_name(store)
        try:
            client = client_for_store(store)
        except Exception as exc:  # noqa: BLE001
            for t in store_targets:
                oid = str(t["order_id"])
                outcomes_by_id[oid] = make_outcome(
                    order_id=oid,
                    state=DARAZ_ERROR,
                    reason=str(exc),
                    daraz_message=str(exc),
                    **_order_fields(oid, t),
                )
            continue

        work: list[tuple[dict[str, Any], list[str], str | None]] = []
        for t in store_targets:
            oid = str(t["order_id"])
            item_ids = list(t.get("order_item_ids") or [])
            if not item_ids:
                outcomes_by_id[oid] = make_outcome(
                    order_id=oid,
                    state=PACKAGE_RESOLUTION_FAILED,
                    reason="no_order_item_ids",
                    **_order_fields(oid, t),
                )
                continue
            work.append((t, item_ids, t.get("package_id")))

        if not work:
            continue

        diag: dict[str, Any] = {
            "store_id": store_uuid,
            "store_slug": sid,
            "target_count": len(work),
            "bulk_attempted": False,
            "bulk_success": False,
            "bulk_format": None,
            "pages": None,
            "fallback_count": 0,
            "fallback_reason": None,
        }

        remaining = list(work)

        # Phase 1 — PrintAWB for package_id targets (native PDF preferred)
        if use_native:
            with_pkg = [(t, ids, pkg) for t, ids, pkg in remaining if pkg]
            without_pkg = [(t, ids, pkg) for t, ids, pkg in remaining if not pkg]
            phase1_failed: list[tuple[dict[str, Any], list[str], str | None]] = []

            if with_pkg:
                progress(f"Gathering labels… PrintAWB for {len(with_pkg)} order(s)")
                workers = min(_print_fetch_workers(), len(with_pkg))
                t_awb = time.perf_counter()
                results: dict[
                    str, tuple[LabelDocument, str, dict[str, Any]] | BaseException
                ] = {}
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(
                            _fetch_print_awb_only,
                            order_id=str(t["daraz_order_id"]),
                            item_ids=item_ids,
                            package_id=str(package_id),
                        ): (t, item_ids, package_id)
                        for t, item_ids, package_id in with_pkg
                    }
                    done = 0
                    for future in as_completed(futures):
                        t, item_ids, package_id = futures[future]
                        oid = str(t["order_id"])
                        done += 1
                        printawb_calls += 1
                        if done == 1 or done == len(with_pkg) or done % 5 == 0:
                            progress(
                                f"Gathering labels… PrintAWB {done}/{len(with_pkg)}"
                            )
                        try:
                            results[oid] = future.result()
                        except BaseException as exc:  # noqa: BLE001
                            results[oid] = exc
                printawb_ms += int((time.perf_counter() - t_awb) * 1000)

                for t, item_ids, package_id in with_pkg:
                    oid = str(t["order_id"])
                    result = results.get(oid)
                    if isinstance(result, BaseException):
                        phase1_failed.append((t, item_ids, package_id))
                        continue
                    if result is None:
                        phase1_failed.append((t, item_ids, package_id))
                        continue
                    label, fetch_source, fetch_meta = result
                    pages_n = _label_pdf_page_count(label) if label.is_pdf() else 1
                    _append_success(
                        t,
                        item_ids,
                        package_id,
                        label=label,
                        fetch_source=fetch_source,
                        fetch_meta=fetch_meta,
                        expected_pages=max(1, pages_n),
                    )
                remaining = without_pkg + phase1_failed
            else:
                remaining = without_pkg

        # Phase 2 — bulk GetDocument for remaining (native path or bulk-first)
        fallback_work = remaining
        if remaining and use_phase2_bulk:
            max_depth = max(1, int(ceil(log2(max(len(remaining), 2)))))
            fallback_work = _run_phase2_bulk(
                remaining, depth=0, diag=diag, max_depth=max_depth
            )

        diag["fallback_count"] = len(fallback_work)
        if fallback_work and not diag.get("fallback_reason"):
            diag["fallback_reason"] = "per_order"
        document_diagnostics.append(diag)

        # Phase 3 prep — per-order PrintAWB/GetDocument for leftovers
        _run_per_order_fallback(fallback_work)

    document_fetch_ms = int((time.perf_counter() - t_fetch) * 1000)

    recorded: list[dict[str, Any]] = []
    pdf_labels: list[LabelDocument] = []
    label_details: list[dict[str, Any]] = []
    out_pdf: Path | None = None
    merge_ms = 0
    record_ms = 0
    html_conversion_ms = 0
    pages = 0

    if pending_successes and raw_labels:
        t_merge = time.perf_counter()
        try:
            html_indices = [
                i for i, label in enumerate(raw_labels) if not label.is_pdf()
            ]
            html_count = len(html_indices)
            if html_count == 0:
                progress("Combining labels…")
                pdf_labels = list(raw_labels)
            else:
                progress(f"Converting {html_count} HTML label(s) to PDF…")
                t_html = time.perf_counter()
                converted_by_index: dict[int, LabelDocument] = {}
                with html_converter_session() as converter:
                    for i, label in enumerate(raw_labels):
                        if label.is_pdf():
                            converted_by_index[i] = label
                        else:
                            converted_by_index[i] = _ensure_pdf_document(
                                label, converter=converter
                            )
                            html_docs_converted += 1
                html_conversion_ms = int((time.perf_counter() - t_html) * 1000)
                pdf_labels = [converted_by_index[i] for i in range(len(raw_labels))]
                progress("Combining labels…")

            out_pdf = output or (OUTPUT_DIR / "combined-labels.pdf")
            out_pdf.parent.mkdir(parents=True, exist_ok=True)
            merge_labels(pdf_labels, out_pdf)
            pages = pdf_page_count(out_pdf)
            merge_ms = int((time.perf_counter() - t_merge) * 1000)

            # Reconcile: never over-credit SUCCESS vs actual PDF pages
            success_batch = list(pending_successes)
            if pages < len(success_batch):
                excess = success_batch[pages:]
                success_batch = success_batch[:pages]
                progress(
                    f"Page mapping: {pages} PDF page(s) for "
                    f"{len(pending_successes)} target(s); "
                    f"marking {len(excess)} as DOCUMENT_MAPPING_FAILED"
                )
                for s in excess:
                    oid = str(s["order_id"])
                    base = _order_fields(oid, s.get("entry"))
                    base["package_id"] = s.get("package_id")
                    base["order_item_ids"] = list(s.get("order_item_ids") or [])
                    outcomes_by_id[oid] = make_outcome(
                        order_id=oid,
                        state=DOCUMENT_MAPPING_FAILED,
                        reason=f"pages_{pages}_lt_targets_{len(pending_successes)}",
                        is_reprint=bool(s.get("is_reprint")),
                        **base,
                    )

            t_rec = time.perf_counter()
            to_record = [
                {
                    "order_id": s["order_id"],
                    "store_id": s["store_id"],
                    "daraz_order_id": s["daraz_order_id"],
                    "order_item_ids": s["order_item_ids"],
                    "package_id": s.get("package_id"),
                    "is_reprint": s.get("is_reprint"),
                    "fetch_source": s.get("fetch_source"),
                }
                for s in success_batch
            ]
            recorded = (
                record_label_prints(workspace_id, user_id, job_id, to_record)
                if to_record
                else []
            )
            record_ms = int((time.perf_counter() - t_rec) * 1000)

            for s in success_batch:
                oid = str(s["order_id"])
                base = _order_fields(oid, s.get("entry"))
                base["package_id"] = s.get("package_id")
                base["order_item_ids"] = list(s.get("order_item_ids") or [])
                outcomes_by_id[oid] = make_outcome(
                    order_id=oid,
                    state=SUCCESS,
                    reason=None,
                    is_reprint=bool(s.get("is_reprint")),
                    **base,
                )

            for label, fetch_source, fetch_meta in zip(
                raw_labels, label_fetch_sources, label_fetch_meta, strict=True
            ):
                notes = fetch_meta.get("print_awb_error")
                if (
                    fetch_meta.get("print_awb_attempted")
                    and fetch_source == "print_awb_pdf"
                ):
                    notes = None
                was_html = not label.is_pdf()
                label_details.append(
                    _label_detail(
                        label,
                        fetch_source,
                        converted=was_html,
                        package_id=fetch_meta.get("package_id"),
                        fetch_notes=notes,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            merge_ms = int((time.perf_counter() - t_merge) * 1000)
            for s in pending_successes:
                oid = str(s["order_id"])
                base = _order_fields(oid, s.get("entry"))
                base["package_id"] = s.get("package_id")
                base["order_item_ids"] = list(s.get("order_item_ids") or [])
                outcomes_by_id[oid] = make_outcome(
                    order_id=oid,
                    state=MERGE_FAILED,
                    reason=str(exc),
                    is_reprint=bool(s.get("is_reprint")),
                    **base,
                )
            recorded = []
            pdf_labels = []
            label_details = []
            out_pdf = None
            pages = 0

    # Every selected order must have an outcome
    for oid in selected:
        if oid not in outcomes_by_id:
            outcomes_by_id[oid] = make_outcome(
                order_id=oid,
                state=UNKNOWN_FAILURE,
                reason="unaccounted",
                **_order_fields(oid),
            )

    outcomes = [outcomes_by_id[oid] for oid in selected]
    summary = summarize_outcomes(outcomes)
    failed_outcomes = [o for o in outcomes if o.get("state") != SUCCESS]

    new_count = sum(
        1
        for o in outcomes
        if o.get("state") == SUCCESS and not o.get("is_reprint")
    )
    reprint_count = sum(
        1 for o in outcomes if o.get("state") == SUCCESS and o.get("is_reprint")
    )

    rel = None
    if out_pdf is not None:
        rel = (
            str(out_pdf.relative_to(PROJECT_ROOT))
            if out_pdf.is_relative_to(PROJECT_ROOT)
            else str(out_pdf)
        )
        rel = rel.replace("\\", "/")

    total_ms = int((time.perf_counter() - t_total) * 1000)
    timings_ms = {
        "hydrate_ms": hydrate_ms,
        "validation_ms": validation_ms,
        "document_fetch_ms": document_fetch_ms,
        "printawb_ms": printawb_ms,
        "bulk_getdocument_ms": bulk_getdocument_ms,
        "fallback_fetch_ms": fallback_fetch_ms,
        "html_conversion_ms": html_conversion_ms,
        "merge_ms": merge_ms,
        "record_ms": record_ms,
        "total_ms": total_ms,
        "printawb_calls": printawb_calls,
        "getdocument_calls": getdocument_calls,
        "bulk_calls": bulk_calls,
        "native_pdf_docs": native_pdf_docs,
        "html_docs_converted": html_docs_converted,
    }

    return {
        "output": str(out_pdf) if out_pdf else None,
        "output_relative": rel,
        "download_url": download_url
        or (f"/api/print-labels/{job_id}/download" if job_id and out_pdf else None),
        "html_url": None,
        "format": "pdf",
        "labels": len(pdf_labels),
        "pages": pages,
        "order_item_count": sum(
            len(o.get("order_item_ids") or [])
            for o in outcomes
            if o.get("state") == SUCCESS
        ),
        "label_details": label_details,
        "label_summary": {
            "pdf_native": sum(1 for d in label_details if not d["converted"]),
            "html_converted": sum(1 for d in label_details if d["converted"]),
            "new_labels_count": new_count,
            "reprint_count": reprint_count,
            "failed_count": summary["failed_count"],
        },
        "new_labels_count": new_count,
        "reprint_count": reprint_count,
        "failed_count": summary["failed_count"],
        "failures": failed_outcomes,
        "failed_order_ids": summary["failed_order_ids"],
        "prints_recorded": len(recorded),
        "outcomes": outcomes,
        "summary": summary,
        "print_status": summary["print_status"],
        "message": summary["message"],
        "timings_ms": timings_ms,
        "document_diagnostics": document_diagnostics,
        "hydrate": {
            "orders_hydrated": hydrate.get("orders_hydrated"),
            "api_calls": hydrate.get("api_calls"),
            "orders_missing": hydrate.get("orders_missing"),
        },
        "validation": {
            "new_printable": len(validated["new_printable"]),
            "already_printed": len(validated["already_printed"]),
            "not_eligible": len(validated["not_eligible"]),
            "errors": validated["errors"],
        },
    }
