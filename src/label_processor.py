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
import json
import logging
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol

from pypdf import PdfReader, PdfWriter

from src.config import LABELS_DIR, OUTPUT_DIR, PROJECT_ROOT, TEST_LABELS_DIR

logger = logging.getLogger(__name__)

DEFAULT_LABEL_WIDTH_MM = 120.0
DEFAULT_LABEL_HEIGHT_MM = 170.0
DARAZ_BODY_CLASS = "cn-html-body"
_MM_RE = re.compile(r"(?P<value>[\d.]+)\s*mm", re.IGNORECASE)

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


def parse_label_page_size_mm(html: str) -> tuple[float, float]:
    """Read Daraz AWB dimensions from cn-html-body inline styles (default 120x170mm)."""
    body_match = re.search(
        rf'class=["\']{DARAZ_BODY_CLASS}["\'][^>]*style=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    style = body_match.group(1) if body_match else html
    width = height = None
    for key, pattern in (
        ("width", re.compile(r"width\s*:\s*([\d.]+)\s*mm", re.IGNORECASE)),
        ("height", re.compile(r"height\s*:\s*([\d.]+)\s*mm", re.IGNORECASE)),
    ):
        match = pattern.search(style)
        if match:
            value = float(match.group(1))
            if key == "width":
                width = value
            else:
                height = value
    return (
        width or DEFAULT_LABEL_WIDTH_MM,
        height or DEFAULT_LABEL_HEIGHT_MM,
    )


def prepare_label_html_for_print(html_bytes: bytes) -> bytes:
    """
    Inject print CSS so AWB fills the page like Seller Center.

    Daraz labels are ~120x170mm and may load layoutPrintRule.js from alicdn.
    """
    html = html_bytes.decode("utf-8", errors="replace")
    width_mm, height_mm = parse_label_page_size_mm(html)
    print_css = f"""
<style id="daraz-print-fix">
@page {{
  size: {width_mm}mm {height_mm}mm;
  margin: 0;
}}
html, body {{
  margin: 0 !important;
  padding: 0 !important;
  width: {width_mm}mm !important;
  height: {height_mm}mm !important;
  overflow: hidden !important;
}}
.{DARAZ_BODY_CLASS} {{
  margin: 0 !important;
}}
</style>
"""
    lower = html.lower()
    if 'id="daraz-print-fix"' in lower:
        return html.encode("utf-8")
    if "<head" in lower:
        html = re.sub(r"(<head[^>]*>)", r"\1" + print_css, html, count=1, flags=re.IGNORECASE)
    else:
        html = print_css + html
    return html.encode("utf-8")


def _mm_to_inches(mm: float) -> float:
    return mm / 25.4


def _chromium_cdp_launch_args(browser_path: Path, port: int) -> list[str]:
    """Minimal Chromium flags for a long-lived CDP printing session."""
    return [
        str(browser_path),
        "--headless",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-software-rasterizer",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        *_chromium_container_flags(),
    ]


def _pick_browser_path() -> Path | None:
    paths = _candidate_browser_paths()
    return paths[0] if paths else None


def _chromium_launch_args(browser_path: Path, *, remote_debugging_port: int | None = None) -> list[str]:
    import os

    args = [
        str(browser_path),
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--run-all-compositor-stages-before-draw",
        "--remote-allow-origins=*",
        *_chromium_container_flags(),
    ]
    if remote_debugging_port is not None:
        args.append(f"--remote-debugging-port={remote_debugging_port}")
    budget = os.environ.get("CHROMIUM_VIRTUAL_TIME_BUDGET", "15000").strip()
    if budget:
        args.append(f"--virtual-time-budget={budget}")
    return args


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


def _chromium_container_flags() -> list[str]:
    """Extra flags for Chromium in Docker/Linux (e.g. Render free tier)."""
    import os
    import sys

    if os.environ.get("CHROMIUM_NO_SANDBOX", "").lower() in {"1", "true", "yes"}:
        return ["--no-sandbox", "--disable-dev-shm-usage"]
    if Path("/.dockerenv").is_file():
        return ["--no-sandbox", "--disable-dev-shm-usage"]
    if sys.platform.startswith("linux"):
        try:
            if os.geteuid() == 0:
                return ["--no-sandbox", "--disable-dev-shm-usage"]
        except AttributeError:
            pass
    return []


class ChromiumHtmlConverter:
    """HTML→PDF via one-shot Edge/Chrome headless --print-to-pdf."""

    def __init__(self, browser_path: str | Path) -> None:
        self.browser_path = Path(browser_path)

    def convert(self, html_bytes: bytes) -> bytes:
        import os

        timeout_s = int(os.environ.get("CHROMIUM_PRINT_TIMEOUT", "60"))
        prepared = prepare_label_html_for_print(html_bytes)
        width_mm, height_mm = parse_label_page_size_mm(prepared.decode("utf-8", errors="replace"))

        with tempfile.TemporaryDirectory(prefix="daraz_label_") as tmp:
            tmp_path = Path(tmp)
            html_path = tmp_path / "label.html"
            pdf_path = tmp_path / "label.pdf"
            profile = tmp_path / f"profile_{uuid.uuid4().hex}"
            profile.mkdir(parents=True, exist_ok=True)
            html_path.write_bytes(prepared)
            file_url = html_path.resolve().as_uri()
            cmd = [
                *_chromium_launch_args(self.browser_path),
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path}",
                file_url,
            ]
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise HtmlConversionError(
                    f"Browser HTML->PDF failed ({self.browser_path.name}): {exc}"
                ) from exc

            if not pdf_path.exists() or pdf_path.stat().st_size == 0:
                stderr = (completed.stderr or completed.stdout or "").strip()
                raise HtmlConversionError(
                    f"Browser did not produce a PDF ({self.browser_path.name}, "
                    f"{width_mm}x{height_mm}mm). {stderr}"
                )
            return pdf_path.read_bytes()


class ChromiumCdpSession:
    """Reuse one Chromium process for multiple label PDFs (much faster than respawning)."""

    def __init__(self, browser_path: str | Path) -> None:
        self.browser_path = Path(browser_path)
        self._proc: subprocess.Popen[str] | None = None
        self._ws = None
        self._msg_id = 0
        self._port: int | None = None
        self._profile_dir: str | None = None

    def start(self) -> None:
        if self._proc is not None:
            return
        self._port = _find_free_port()
        self._profile_dir = tempfile.mkdtemp(prefix="daraz_chromium_")
        cmd = _chromium_cdp_launch_args(self.browser_path, self._port)
        cmd.append(f"--user-data-dir={self._profile_dir}")
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_devtools(self._port, self._proc)
        page_ws = _open_page_target(self._port)
        try:
            from websocket import create_connection
        except ImportError as exc:
            raise HtmlConversionError(
                "websocket-client is required for fast label PDF conversion. "
                "Run: pip install websocket-client"
            ) from exc
        self._ws = create_connection(page_ws, timeout=30)

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._profile_dir:
            import shutil

            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None

    def __enter__(self) -> ChromiumCdpSession:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def convert(self, html_bytes: bytes) -> bytes:
        if self._ws is None:
            self.start()
        assert self._ws is not None

        prepared = prepare_label_html_for_print(html_bytes)
        html = prepared.decode("utf-8", errors="replace")
        width_mm, height_mm = parse_label_page_size_mm(html)

        with tempfile.TemporaryDirectory(prefix="daraz_label_") as tmp:
            html_path = Path(tmp) / "label.html"
            html_path.write_bytes(prepared)
            file_url = html_path.resolve().as_uri()
            self._navigate(file_url)
            result = self._call(
                "Page.printToPDF",
                {
                    "printBackground": True,
                    "preferCSSPageSize": True,
                    "marginTop": 0,
                    "marginBottom": 0,
                    "marginLeft": 0,
                    "marginRight": 0,
                    "paperWidth": _mm_to_inches(width_mm),
                    "paperHeight": _mm_to_inches(height_mm),
                    "scale": 1,
                },
            )
        data = result.get("data")
        if not data:
            raise HtmlConversionError("Chromium CDP printToPDF returned no data")
        return base64.b64decode(data)

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _call(self, method: str, params: dict | None = None, *, timeout: float = 90) -> dict:
        if self._ws is None:
            raise HtmlConversionError("Chromium CDP session is not started")
        msg_id = self._next_id()
        self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._ws.settimeout(max(0.2, deadline - time.monotonic()))
            try:
                raw = self._ws.recv()
            except Exception:
                continue
            data = json.loads(raw)
            if data.get("id") != msg_id:
                continue
            if "error" in data:
                raise HtmlConversionError(f"Chromium CDP {method} failed: {data['error']}")
            return data.get("result") or {}
        raise HtmlConversionError(f"Chromium CDP {method} timed out")

    def _navigate(self, url: str) -> None:
        import os

        self._call("Page.navigate", {"url": url}, timeout=30)
        extra_wait = float(
            os.environ.get(
                "CHROMIUM_LABEL_JS_WAIT",
                "1.2" if os.environ.get("CHROMIUM_NO_SANDBOX") else "3.0",
            )
        )
        time.sleep(extra_wait)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_devtools(port: int, proc: subprocess.Popen[str], *, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    version_url = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = (proc.stderr.read() if proc.stderr else "") or "process exited"
            raise HtmlConversionError(f"Chromium failed to start: {stderr}")
        try:
            with urllib.request.urlopen(version_url, timeout=2):
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.15)
    raise HtmlConversionError("Timed out waiting for Chromium DevTools")


def _open_page_target(port: int) -> str:
    new_url = f"http://127.0.0.1:{port}/json/new"
    req = urllib.request.Request(new_url, method="PUT", data=b"")
    with urllib.request.urlopen(req, timeout=5) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    ws_url = payload.get("webSocketDebuggerUrl")
    if not ws_url:
        raise HtmlConversionError("Could not open Chromium page target")
    return str(ws_url)


@contextmanager
def html_converter_session() -> Iterator[HtmlToPdfConverter]:
    """
    Prefer a reused Chromium CDP session (fast batch printing).
    Falls back to one-shot subprocess conversion if CDP is unavailable.
    """
    browser = _pick_browser_path()
    if browser is None:
        yield get_html_converter()
        return

    session = ChromiumCdpSession(browser)
    try:
        session.start()
        yield session
    except Exception as exc:
        logger.warning("Chromium CDP session unavailable, using slow fallback: %s", exc)
        yield ChromiumHtmlConverter(browser)
    finally:
        session.close()


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
