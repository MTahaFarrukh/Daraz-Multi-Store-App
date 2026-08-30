"""Map Daraz document API payloads onto label_processor documents."""

from __future__ import annotations

from typing import Any

from src.label_processor import LabelDocument, LabelMetadata, decode_label_document


def _label_document_from_file(
    *,
    file_b64: str,
    mime_type: str,
    store_id: str,
    store_name: str,
    order_id: str,
    order_item_ids: list[str],
) -> LabelDocument:
    item_id = order_item_ids[0] if order_item_ids else "unknown"
    ext = "pdf" if "pdf" in mime_type.lower() else "html"
    filename = f"{order_id}__{item_id}.{ext}"
    return decode_label_document(
        mime_type=mime_type,
        base64_content=str(file_b64),
        metadata=LabelMetadata(
            store_id=store_id,
            store_name=store_name,
            order_id=str(order_id),
            order_item_id=str(item_id),
            source_filename=filename,
        ),
    )


def document_from_print_awb_response(
    doc_resp: dict[str, Any],
    *,
    store_id: str,
    store_name: str,
    order_id: str,
    order_item_ids: list[str],
) -> LabelDocument:
    """Build a LabelDocument from PrintAWB / package document get response."""
    data = doc_resp.get("data") or {}
    if not data and isinstance(doc_resp.get("result"), dict):
        data = doc_resp["result"].get("data") or {}

    doc_type = str(data.get("doc_type") or data.get("DocType") or "PDF")
    mime_type = (
        "application/pdf"
        if "pdf" in doc_type.lower()
        else "text/html"
    )
    file_b64 = data.get("file") or data.get("File") or ""
    return _label_document_from_file(
        file_b64=str(file_b64),
        mime_type=mime_type,
        store_id=store_id,
        store_name=store_name,
        order_id=order_id,
        order_item_ids=order_item_ids,
    )


def document_from_daraz_response(
    doc_resp: dict[str, Any],
    *,
    store_id: str,
    store_name: str,
    order_id: str,
    order_item_ids: list[str],
) -> LabelDocument:
    """
    Build a LabelDocument from a GetDocument / get_shipping_label response.

    Uses the first order_item_id for metadata when multiple items share one file.
    """
    document = (doc_resp.get("data") or {}).get("document") or {}
    mime_type = document.get("mime_type") or document.get("MimeType") or "application/octet-stream"
    file_b64 = document.get("file") or document.get("File") or ""
    return _label_document_from_file(
        file_b64=str(file_b64),
        mime_type=str(mime_type),
        store_id=store_id,
        store_name=store_name,
        order_id=order_id,
        order_item_ids=order_item_ids,
    )
