"""Tests for print label source metadata."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from src.label_processor import LabelDocument
from src.ops import _disk_fetch_source, _label_detail


def _pdf_label() -> LabelDocument:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return LabelDocument(
        store_id="s1",
        store_name="Store One",
        order_id="111",
        order_item_id="222",
        source_filename="111__222.pdf",
        mime_type="application/pdf",
        document_bytes=buf.getvalue(),
    )


def test_label_detail_print_awb_pdf() -> None:
    detail = _label_detail(_pdf_label(), "print_awb_pdf", converted=False)
    assert detail["display"] == "Daraz PDF (PrintAWB)"
    assert detail["kind"] == "pdf"
    assert detail["converted"] is False


def test_label_detail_html_converted() -> None:
    html = LabelDocument(
        store_id="s1",
        store_name="Store One",
        order_id="333",
        order_item_id="444",
        source_filename="333__444.html",
        mime_type="text/html",
        document_bytes=b"<html><body>x</body></html>",
    )
    detail = _label_detail(html, "get_document_html", converted=True)
    assert detail["display"] == "Daraz HTML → converted"
    assert detail["kind"] == "converted"
    assert detail["converted"] is True


def test_disk_fetch_source() -> None:
    assert _disk_fetch_source(_pdf_label()) == "saved_pdf"


def test_resolve_stores_by_ids(monkeypatch) -> None:
    from src import ops

    monkeypatch.setattr(
        ops,
        "get_store",
        lambda sid: {"store_id": sid, "access_token": "t"} if sid == "a" else None,
    )
    with pytest.raises(ValueError, match="Unknown store"):
        ops.resolve_stores(store_ids=["a", "missing"])
    stores = ops.resolve_stores(store_ids=["a"])
    assert len(stores) == 1
    assert stores[0]["store_id"] == "a"
