"""Tests for label_processor — no Daraz API, local documents only."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from src.label_processor import (
    HtmlConversionError,
    InvalidBase64Error,
    LabelDocument,
    LabelMetadata,
    UnsupportedMimeTypeError,
    UnavailableHtmlConverter,
    decode_base64_content,
    decode_label_document,
    default_label_sort_key,
    html_to_pdf,
    merge_labels,
    parse_label_page_size_mm,
    prepare_label_html_for_print,
    sort_labels,
)


def _single_page_pdf(page_width: float = 200, page_height: float = 300) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=page_width, height=page_height)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _sample_metadata(**overrides) -> LabelMetadata:
    defaults = {
        "store_id": "store_a",
        "store_name": "Store A",
        "order_id": "ORDER-001",
        "order_item_id": "ITEM-001",
        "source_filename": "ORDER-001__ITEM-001.pdf",
    }
    defaults.update(overrides)
    return LabelMetadata(**defaults)


def test_base64_pdf_decoding_works() -> None:
    pdf_bytes = _single_page_pdf()
    encoded = base64.b64encode(pdf_bytes).decode("ascii")
    label = decode_label_document(
        "application/pdf",
        encoded,
        _sample_metadata(),
    )
    assert label.document_bytes == pdf_bytes
    assert label.normalized_mime_type() == "application/pdf"


def test_invalid_base64_raises_clear_error() -> None:
    with pytest.raises(InvalidBase64Error, match="Invalid base64"):
        decode_base64_content("not!!!valid!!!base64")


def test_pdf_labels_can_be_merged(tmp_path: Path) -> None:
    labels = [
        LabelDocument(
            store_id="store_a",
            store_name="Store A",
            order_id="ORDER-001",
            order_item_id="ITEM-001",
            source_filename="a.pdf",
            mime_type="application/pdf",
            document_bytes=_single_page_pdf(200, 300),
        ),
        LabelDocument(
            store_id="store_b",
            store_name="Store B",
            order_id="ORDER-002",
            order_item_id="ITEM-002",
            source_filename="b.pdf",
            mime_type="application/pdf",
            document_bytes=_single_page_pdf(400, 500),
        ),
    ]
    output = tmp_path / "merged.pdf"
    merge_labels(labels, output)
    assert output.exists()
    assert len(PdfReader(output.open("rb")).pages) == 2


def test_five_labels_produce_five_page_combined_pdf(tmp_path: Path) -> None:
    labels = [
        LabelDocument(
            store_id=f"store_{i}",
            store_name=f"Store {i}",
            order_id=f"ORDER-{i:03d}",
            order_item_id=f"ITEM-{i:03d}",
            source_filename=f"label{i}.pdf",
            mime_type="application/pdf",
            document_bytes=_single_page_pdf(200 + i * 10, 300 + i * 10),
        )
        for i in range(1, 6)
    ]
    output = tmp_path / "combined.pdf"
    merge_labels(labels, output)
    assert len(PdfReader(output.open("rb")).pages) == 5


def test_output_pdf_readable_by_pypdf(tmp_path: Path) -> None:
    label = LabelDocument(
        store_id="store_a",
        store_name="Store A",
        order_id="ORDER-001",
        order_item_id="ITEM-001",
        source_filename="one.pdf",
        mime_type="application/pdf",
        document_bytes=_single_page_pdf(),
    )
    output = tmp_path / "out.pdf"
    merge_labels([label], output)
    reader = PdfReader(output.open("rb"))
    page = reader.pages[0]
    assert float(page.mediabox.width) == pytest.approx(200.0)
    assert float(page.mediabox.height) == pytest.approx(300.0)


def test_input_ordering_is_deterministic() -> None:
    labels = [
        LabelDocument("store_c", "Store C", "ORDER-005", "ITEM-005", "c.pdf", "application/pdf", b"%PDF"),
        LabelDocument("store_a", "Store A", "ORDER-002", "ITEM-002", "a2.pdf", "application/pdf", b"%PDF"),
        LabelDocument("store_a", "Store A", "ORDER-001", "ITEM-001", "a1.pdf", "application/pdf", b"%PDF"),
        LabelDocument("store_b", "Store B", "ORDER-004", "ITEM-004", "b2.pdf", "application/pdf", b"%PDF"),
        LabelDocument("store_b", "Store B", "ORDER-003", "ITEM-003", "b1.pdf", "application/pdf", b"%PDF"),
    ]
    ordered = sort_labels(labels)
    keys = [(l.store_name, l.order_id, l.order_item_id) for l in ordered]
    assert keys == [
        ("Store A", "ORDER-001", "ITEM-001"),
        ("Store A", "ORDER-002", "ITEM-002"),
        ("Store B", "ORDER-003", "ITEM-003"),
        ("Store B", "ORDER-004", "ITEM-004"),
        ("Store C", "ORDER-005", "ITEM-005"),
    ]
    assert default_label_sort_key(ordered[0]) == ("Store A", "ORDER-001", "ITEM-001")


def test_metadata_remains_associated_during_processing(tmp_path: Path) -> None:
    labels = [
        LabelDocument(
            store_id="store_a",
            store_name="Store A",
            order_id="ORDER-001",
            order_item_id="ITEM-001",
            source_filename="ORDER-001__ITEM-001.pdf",
            mime_type="application/pdf",
            document_bytes=_single_page_pdf(),
        ),
        LabelDocument(
            store_id="store_b",
            store_name="Store B",
            order_id="ORDER-003",
            order_item_id="ITEM-003",
            source_filename="ORDER-003__ITEM-003.pdf",
            mime_type="application/pdf",
            document_bytes=_single_page_pdf(),
        ),
    ]
    ordered = sort_labels(labels)
    assert ordered[0].store_id == "store_a"
    assert ordered[0].order_id == "ORDER-001"
    assert ordered[1].store_id == "store_b"
    assert ordered[1].order_item_id == "ITEM-003"

    output = tmp_path / "meta.pdf"
    merge_labels(ordered, output)
    assert output.exists()
    for label in ordered:
        assert label.store_name
        assert label.order_id
        assert label.order_item_id


def test_unsupported_mime_type_raises_clear_error() -> None:
    encoded = base64.b64encode(b"hello").decode("ascii")
    with pytest.raises(UnsupportedMimeTypeError, match="Unsupported mime_type"):
        decode_label_document("application/json", encoded, _sample_metadata())


def test_parse_daraz_label_dimensions_from_body_style() -> None:
    html = (
        '<div class="cn-html-body" style="height: 170mm; width: 120mm; overflow:hidden;">'
        "<span>AWB</span></div>"
    )
    assert parse_label_page_size_mm(html) == (120.0, 170.0)


def test_prepare_label_html_injects_full_page_css() -> None:
    html = (
        b'<div class="cn-html-body" style="height: 170mm; width: 120mm;">label</div>'
    )
    prepared = prepare_label_html_for_print(html).decode("utf-8")
    assert "@page" in prepared
    assert "120.0mm 170.0mm" in prepared
    assert "daraz-print-fix" in prepared


def test_html_conversion_raises_when_unavailable() -> None:
    html = b"<html><body><h1>Test</h1></body></html>"
    with pytest.raises(HtmlConversionError):
        html_to_pdf(html, converter=UnavailableHtmlConverter())

    label = LabelDocument(
        store_id="store_a",
        store_name="Store A",
        order_id="ORDER-001",
        order_item_id="ITEM-001",
        source_filename="label.html",
        mime_type="text/html",
        document_bytes=html,
    )
    with pytest.raises(HtmlConversionError):
        label.as_pdf_bytes(html_converter=UnavailableHtmlConverter())
