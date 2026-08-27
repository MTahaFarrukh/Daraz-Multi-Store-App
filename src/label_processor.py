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

from src.config import LABELS_DIR, OUTPUT_DIR, PROJECT_ROOT, TEST_LABELS_DIR

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

    Prefer Edge/Chrome headless on Windows, or WeasyPrint where GTK is available.
    See docs/LABEL_PROCESSING.md / docs/PHASE3_CLI.md.
    """

    def convert(self, html_bytes: bytes) -> bytes:
        raise HtmlConversionError(
            "HTML to PDF conversion is not available in this environment. "
            "Install Microsoft Edge or Google Chrome (used headless), or WeasyPrint. "
            "See docs/PHASE3_CLI.md."
        )


class ChromiumHtmlConverter:
    """HTML→PDF via Edge/Chrome headless --print-to-pdf (Windows-friendly)."""

    def __init__(self, browser_path: str | Path) -> None:
        self.browser_path = Path(browser_path)

    def convert(self, html_bytes: bytes) -> bytes:
        import subprocess
        import tempfile
        import uuid

        with tempfile.TemporaryDirectory(prefix="daraz_label_") as tmp:
            tmp_path = Path(tmp)
            html_path = tmp_path / "label.html"
            pdf_path = tmp_path / "label.pdf"
            profile = tmp_path / f"profile_{uuid.uuid4().hex}"
            profile.mkdir(parents=True, exist_ok=True)
            html_path.write_bytes(html_bytes)
            file_url = html_path.resolve().as_uri()
            # Unique user-data-dir avoids Edge lock hangs across conversions.
            cmd = [
                str(self.browser_path),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-javascript",
                "--disable-background-networking",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path}",
                file_url,
            ]
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise HtmlConversionError(
                    f"Browser HTML->PDF failed ({self.browser_path.name}): {exc}"
                ) from exc

            if not pdf_path.exists() or pdf_path.stat().st_size == 0:
                stderr = (completed.stderr or completed.stdout or "").strip()
                raise HtmlConversionError(
                    f"Browser did not produce a PDF ({self.browser_path.name}). {stderr}"
                )
            return pdf_path.read_bytes()


def _candidate_browser_paths() -> list[Path]:
    import os
    import shutil

    found: list[Path] = []
    chromium_env = os.environ.get("CHROMIUM_PATH", "").strip()
    if chromium_env and Path(chromium_env).is_file():
        found.append(Path(chromium_env))

    names = ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge")
    for name in names:
        which = shutil.which(name)
        if which:
            found.append(Path(which))

    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    candidates = [
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/usr/bin/google-chrome"),
        Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for path in candidates:
        if path.is_file():
            found.append(path)
    # Dedupe while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for path in found:
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


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
    """Return Edge/Chrome headless if found, else WeasyPrint, else unavailable stub."""
    for browser in _candidate_browser_paths():
        return ChromiumHtmlConverter(browser)
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
