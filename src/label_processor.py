"""
Shipping-label document processing engine.

Accepts decoded or base64-encoded PDF/HTML label documents, normalizes them
to PDF bytes, and merges multiple labels into a single printable PDF.

No Daraz API calls — designed for future integration with GetDocument responses.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from pypdf import PdfReader, PdfWriter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_LABELS_DIR = PROJECT_ROOT / "data" / "test_labels"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

SUPPORTED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/x-pdf",
        "text/html",
        "text/html; charset=utf-8",
    }
)

FILENAME_PATTERN = re.compile(
    r"^(?P<order_id>.+)__(?P<order_item_id>.+)\.(pdf|html)$",
    re.IGNORECASE,
)


class LabelProcessingError(Exception):
    """Base error for label processing failures."""


class InvalidBase64Error(LabelProcessingError):
    """Raised when base64 content cannot be decoded."""


class UnsupportedMimeTypeError(LabelProcessingError):
    """Raised when mime_type is not supported."""


class HtmlConversionError(LabelProcessingError):
    """Raised when HTML cannot be converted to PDF."""


@dataclass(frozen=True, slots=True)
class LabelDocument:
    """A shipping label document with store and order metadata."""

    store_id: str
    store_name: str
    order_id: str
    order_item_id: str
    source_filename: str
    mime_type: str
    document_bytes: bytes

    def normalized_mime_type(self) -> str:
        return self.mime_type.split(";")[0].strip().lower()

    def is_pdf(self) -> bool:
        mime = self.normalized_mime_type()
        return mime in {"application/pdf", "application/x-pdf"} or self.document_bytes.startswith(
            b"%PDF"
        )

    def is_html(self) -> bool:
        mime = self.normalized_mime_type()
        if mime == "text/html":
            return True
        sample = self.document_bytes.lstrip()[:256].lower()
        return sample.startswith(b"<") or b"<html" in sample

    def as_pdf_bytes(self, html_converter: HtmlToPdfConverter | None = None) -> bytes:
        """Return PDF bytes, converting HTML labels when a converter is available."""
        if self.is_pdf():
            return self.document_bytes
        if self.is_html():
            converter = html_converter or get_html_converter()
            return converter.convert(self.document_bytes)
        raise UnsupportedMimeTypeError(
            f"Cannot convert mime_type={self.mime_type!r} to PDF for {self.source_filename}"
        )

    def page_count(self, html_converter: HtmlToPdfConverter | None = None) -> int:
        pdf_bytes = self.as_pdf_bytes(html_converter=html_converter)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return len(reader.pages)


@dataclass
class LabelMetadata:
    """Metadata supplied when decoding a Daraz-style base64 document."""

    store_id: str
    store_name: str
    order_id: str
    order_item_id: str
    source_filename: str = "document"


class HtmlToPdfConverter(Protocol):
    def convert(self, html_bytes: bytes) -> bytes: ...


class UnavailableHtmlConverter:
    """
    Placeholder converter when no HTML rendering backend is installed.

    WeasyPrint on Windows typically requires extra system libraries (GTK/Pango).
    Install optionally: pip install weasyprint
    See docs/LABEL_PROCESSING.md for details.
    """

    def convert(self, html_bytes: bytes) -> bytes:
        raise HtmlConversionError(
            "HTML to PDF conversion is not available in this environment. "
            "Install WeasyPrint and its system dependencies, or pre-convert HTML "
            "labels to PDF before merging. See docs/LABEL_PROCESSING.md."
        )


class WeasyPrintHtmlConverter:
    """Optional HTML→PDF backend using WeasyPrint."""

    def convert(self, html_bytes: bytes) -> bytes:
        try:
            from weasyprint import HTML  # type: ignore[import-untyped]
        except ImportError as exc:
            raise HtmlConversionError(
                "WeasyPrint is not installed. Run: pip install weasyprint"
            ) from exc
        except OSError as exc:
            raise HtmlConversionError(
                "WeasyPrint is installed but system dependencies are missing "
                f"(common on Windows): {exc}"
            ) from exc

        return HTML(string=html_bytes.decode("utf-8")).write_pdf()


def get_html_converter() -> HtmlToPdfConverter:
    """Return the best available HTML converter, or a clear unavailable stub."""
    try:
        from weasyprint import HTML  # noqa: F401

        return WeasyPrintHtmlConverter()
    except (ImportError, OSError):
        return UnavailableHtmlConverter()


def html_to_pdf(html_bytes: bytes, converter: HtmlToPdfConverter | None = None) -> bytes:
    """Convert HTML label bytes to PDF using the configured converter."""
    impl = converter or get_html_converter()
    return impl.convert(html_bytes)


def decode_base64_content(base64_content: str) -> bytes:
    """Decode base64 label content with clear errors."""
    if not base64_content or not base64_content.strip():
        raise InvalidBase64Error("Base64 content is empty")
    try:
        return base64.b64decode(base64_content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidBase64Error(f"Invalid base64 content: {exc}") from exc


def decode_label_document(
    mime_type: str,
    base64_content: str,
    metadata: LabelMetadata,
) -> LabelDocument:
    """
    Build a LabelDocument from a Daraz GetDocument-style payload.

    Future adapter:
        document = response["data"]["document"]
        decode_label_document(
            mime_type=document["mime_type"],
            base64_content=document["file"],
            metadata=LabelMetadata(...),
        )
    """
    normalized = mime_type.split(";")[0].strip().lower()
    if normalized not in {m.split(";")[0] for m in SUPPORTED_MIME_TYPES}:
        raise UnsupportedMimeTypeError(
            f"Unsupported mime_type={mime_type!r}. "
            f"Supported: application/pdf, text/html"
        )

    document_bytes = decode_base64_content(base64_content)
    return LabelDocument(
        store_id=metadata.store_id,
        store_name=metadata.store_name,
        order_id=metadata.order_id,
        order_item_id=metadata.order_item_id,
        source_filename=metadata.source_filename,
        mime_type=mime_type,
        document_bytes=document_bytes,
    )


def default_label_sort_key(label: LabelDocument) -> tuple[str, str, str]:
    """Deterministic ordering: store name → order id → order item id."""
    return (label.store_name, label.order_id, label.order_item_id)


def sort_labels(
    labels: list[LabelDocument],
    key: Callable[[LabelDocument], tuple[str, str, str]] | None = None,
) -> list[LabelDocument]:
    """Return labels sorted for merge; custom key supported for future UI ordering."""
    sort_key = key or default_label_sort_key
    return sorted(labels, key=sort_key)


def merge_labels(
    labels: list[LabelDocument],
    output_path: str | Path,
    *,
    html_converter: HtmlToPdfConverter | None = None,
    sort_key: Callable[[LabelDocument], tuple[str, str, str]] | None = None,
) -> Path:
    """
    Merge multiple label PDFs into one file, preserving page content and dimensions.

    Labels are merged in deterministic order unless sort_key is overridden.
    HTML labels are converted to PDF when a converter is available.
    """
    if not labels:
        raise LabelProcessingError("No labels provided for merge")

    ordered = sort_labels(labels, key=sort_key)
    writer = PdfWriter()
    total_pages = 0

    for label in ordered:
        pdf_bytes = label.as_pdf_bytes(html_converter=html_converter)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)
            total_pages += 1

    if total_pages == 0:
        raise LabelProcessingError("Merged PDF would contain zero pages")

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        writer.write(handle)
    return destination


def load_label_from_file(
    path: Path,
    *,
    store_id: str,
    store_name: str,
) -> LabelDocument:
    """Load a label file from disk with metadata parsed from filename."""
    match = FILENAME_PATTERN.match(path.name)
    if not match:
        raise LabelProcessingError(
            f"Filename must match ORDER-ID__ITEM-ID.pdf|.html, got: {path.name}"
        )

    suffix = path.suffix.lower()
    mime_type = "application/pdf" if suffix == ".pdf" else "text/html"
    document_bytes = path.read_bytes()

    return LabelDocument(
        store_id=store_id,
        store_name=store_name,
        order_id=match.group("order_id"),
        order_item_id=match.group("order_item_id"),
        source_filename=path.name,
        mime_type=mime_type,
        document_bytes=document_bytes,
    )


def discover_test_labels(
    root: Path | None = None,
    store_names: dict[str, str] | None = None,
) -> list[LabelDocument]:
    """Discover test labels under data/test_labels/{store_id}/."""
    labels_root = root or TEST_LABELS_DIR
    if not labels_root.exists():
        return []

    names = store_names or {}
    labels: list[LabelDocument] = []

    for store_dir in sorted(labels_root.iterdir()):
        if not store_dir.is_dir():
            continue
        store_id = store_dir.name
        store_name = names.get(store_id, store_id.replace("_", " ").title())

        for path in sorted(store_dir.iterdir()):
            if path.suffix.lower() not in {".pdf", ".html"}:
                continue
            labels.append(
                load_label_from_file(path, store_id=store_id, store_name=store_name)
            )

    return sort_labels(labels)


def pdf_page_count(path: Path) -> int:
    """Return page count for a PDF file."""
    reader = PdfReader(path.open("rb"))
    return len(reader.pages)
