"""Map Daraz document API payloads onto label_processor documents."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable
from typing import Any

from src.daraz_api import DarazApiError
from src.label_processor import LabelDocument, LabelMetadata, decode_label_document

_IFRAME_SRC_RE = re.compile(
    r"""<iframe[^>]+src=["']([^"']+)["']""",
    re.IGNORECASE,
)
_PDF_URL_RE = re.compile(r"""https?://[^\s"'<>]+\.pdf[^\s"'<>]*""", re.IGNORECASE)


def _label_document_from_bytes(
    *,
    content: bytes,
    mime_type: str,
    store_id: str,
    store_name: str,
    order_id: str,
    order_item_ids: list[str],
) -> LabelDocument:
    item_id = order_item_ids[0] if order_item_ids else "unknown"
    ext = "pdf" if "pdf" in mime_type.lower() or content.startswith(b"%PDF") else "html"
    filename = f"{order_id}__{item_id}.{ext}"
    encoded = base64.b64encode(content).decode("ascii")
    return decode_label_document(
        mime_type=mime_type,
        base64_content=encoded,
        metadata=LabelMetadata(
            store_id=store_id,
            store_name=store_name,
            order_id=str(order_id),
            order_item_id=str(item_id),
            source_filename=filename,
        ),
    )


def _label_document_from_file(
    *,
    file_b64: str,
    mime_type: str,
    store_id: str,
    store_name: str,
    order_id: str,
    order_item_ids: list[str],
) -> LabelDocument:
    return decode_label_document(
        mime_type=mime_type,
        base64_content=str(file_b64),
        metadata=LabelMetadata(
            store_id=store_id,
            store_name=store_name,
            order_id=str(order_id),
            order_item_id=str(order_item_ids[0] if order_item_ids else "unknown"),
            source_filename=(
                f"{order_id}__{order_item_ids[0] if order_item_ids else 'unknown'}."
                f"{'pdf' if 'pdf' in mime_type.lower() else 'html'}"
            ),
        ),
    )


def unwrap_print_awb_data(doc_resp: dict[str, Any]) -> dict[str, Any]:
    """Normalize PrintAWB / package document responses (Lazada-style result wrapper)."""
    raw_result = doc_resp.get("result")
    if isinstance(raw_result, str) and raw_result.strip():
        try:
            parsed = json.loads(raw_result)
        except json.JSONDecodeError as exc:
            raise DarazApiError("Invalid PrintAWB result JSON") from exc
        if isinstance(parsed, dict):
            raw_result = parsed

    if isinstance(raw_result, dict):
        success = raw_result.get("success")
        if success is False:
            raise DarazApiError(
                str(
                    raw_result.get("error_msg")
                    or raw_result.get("errorMsg")
                    or raw_result.get("error_code")
                    or raw_result.get("errorCode")
                    or "PrintAWB failed"
                ),
                payload=raw_result,
            )
        data = raw_result.get("data")
        if isinstance(data, dict):
            return data

    data = doc_resp.get("data")
    if isinstance(data, dict):
        return data
    return {}


def extract_pdf_url_from_html(html: str) -> str | None:
    match = _IFRAME_SRC_RE.search(html)
    if match:
        return match.group(1).strip()
    match = _PDF_URL_RE.search(html)
    if match:
        return match.group(0).strip()
    return None


def resolve_print_awb_pdf_bytes(
    data: dict[str, Any],
    *,
    download_url: Callable[[str], bytes] | None = None,
) -> bytes | None:
    """
    Resolve native PDF bytes from PrintAWB data.

    Lazada often returns doc_type=PDF with either raw PDF bytes, a pdf_url, or an
    HTML iframe wrapper — not always application/pdf in the file field.
    """
    pdf_url = data.get("pdf_url") or data.get("pdfUrl")
    if pdf_url and download_url:
        content = download_url(str(pdf_url))
        if content.startswith(b"%PDF"):
            return content

    file_b64 = data.get("file") or data.get("File") or ""
    if not file_b64:
        return None

    try:
        raw = base64.b64decode(file_b64, validate=False)
    except (binascii.Error, ValueError):
        return None

    if raw.startswith(b"%PDF"):
        return raw

    sample = raw[:512].lower()
    if b"<iframe" in sample or b"<html" in sample or sample.lstrip().startswith(b"<"):
        html = raw.decode("utf-8", errors="replace")
        embedded_url = extract_pdf_url_from_html(html)
        if embedded_url and download_url:
            content = download_url(embedded_url)
            if content.startswith(b"%PDF"):
                return content
    return None


def document_from_print_awb_response(
    doc_resp: dict[str, Any],
    *,
    store_id: str,
    store_name: str,
    order_id: str,
    order_item_ids: list[str],
    download_url: Callable[[str], bytes] | None = None,
) -> LabelDocument:
    """Build a LabelDocument from PrintAWB / package document get response."""
    data = unwrap_print_awb_data(doc_resp)
    pdf_bytes = resolve_print_awb_pdf_bytes(data, download_url=download_url)
    if pdf_bytes:
        return _label_document_from_bytes(
            content=pdf_bytes,
            mime_type="application/pdf",
            store_id=store_id,
            store_name=store_name,
            order_id=order_id,
            order_item_ids=order_item_ids,
        )

    doc_type = str(data.get("doc_type") or data.get("DocType") or "PDF")
    mime_type = "application/pdf" if "pdf" in doc_type.lower() else "text/html"
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
