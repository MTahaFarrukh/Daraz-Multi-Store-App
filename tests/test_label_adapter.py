"""Tests for PrintAWB PDF resolution."""

from __future__ import annotations

import base64
import io

from pypdf import PdfWriter

from src.label_adapter import (
    document_from_print_awb_response,
    extract_pdf_url_from_html,
    resolve_print_awb_pdf_bytes,
    unwrap_print_awb_data,
)


def _single_page_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_unwrap_print_awb_result_string() -> None:
    data = unwrap_print_awb_data(
        {
            "result": (
                '{"data":{"doc_type":"PDF","file":"abc"},"success":true,'
                '"errorCode":"","errorMsg":""}'
            )
        }
    )
    assert data["doc_type"] == "PDF"


def test_resolve_print_awb_pdf_url() -> None:
    pdf = _single_page_pdf()
    url = "https://example.com/label.pdf"

    def download(target: str) -> bytes:
        assert target == url
        return pdf

    data = {"doc_type": "PDF", "pdf_url": url, "file": ""}
    assert resolve_print_awb_pdf_bytes(data, download_url=download) == pdf


def test_resolve_print_awb_iframe_html() -> None:
    pdf = _single_page_pdf()
    url = "https://example.com/embedded.pdf"
    html = f'<html><body><iframe src="{url}"></iframe></body></html>'
    encoded = base64.b64encode(html.encode()).decode()

    def download(target: str) -> bytes:
        assert target == url
        return pdf

    data = {"doc_type": "PDF", "file": encoded}
    assert resolve_print_awb_pdf_bytes(data, download_url=download) == pdf


def test_extract_pdf_url_from_html() -> None:
    html = '<iframe src="https://cdn.example.com/a.pdf?sig=1"></iframe>'
    assert extract_pdf_url_from_html(html) == "https://cdn.example.com/a.pdf?sig=1"


def test_document_from_print_awb_pdf_url() -> None:
    pdf = _single_page_pdf()
    url = "https://example.com/label.pdf"
    doc = document_from_print_awb_response(
        {
            "result": {
                "data": {"doc_type": "PDF", "pdf_url": url, "file": ""},
                "success": True,
            }
        },
        store_id="s1",
        store_name="Store",
        order_id="111",
        order_item_ids=["222"],
        download_url=lambda _: pdf,
    )
    assert doc.is_pdf()
