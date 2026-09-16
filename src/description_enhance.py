"""Sanitize and optionally enhance product description HTML for clone drafts."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

_ALLOWED_TAGS = frozenset(
    {
        "article",
        "div",
        "p",
        "span",
        "ul",
        "ol",
        "li",
        "br",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "img",
    }
)
_VOID = frozenset({"br", "img"})
_IMG_SRC_HOSTS = (
    "daraz.pk",
    "daraz.com",
    "slatic.net",
    "alicdn.com",
    "lazada.",
)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip_depth = 0
        self.image_srcs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "link", "meta"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag not in _ALLOWED_TAGS:
            return
        if tag == "img":
            src = ""
            alt = ""
            for k, v in attrs:
                if k.lower() == "src" and v:
                    src = v.strip()
                if k.lower() == "alt" and v:
                    alt = v.strip()
            if not src or not _safe_img_src(src):
                return
            self.image_srcs.append(src)
            esc_src = _escape_attr(src)
            esc_alt = _escape_attr(alt)
            self._out.append(f'<img src="{esc_src}" alt="{esc_alt}" />')
            return
        # Drop event handlers / style for safety (keep structure only)
        self._out.append(f"<{tag}>")
        if tag in _VOID:
            return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "link", "meta"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth or tag not in _ALLOWED_TAGS or tag in _VOID:
            return
        self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._out.append(_escape_text(data))

    def result(self) -> str:
        return "".join(self._out)


def _safe_img_src(src: str) -> bool:
    if src.startswith("data:"):
        return False
    if src.startswith("//"):
        src = "https:" + src
    try:
        parsed = urlparse(src)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return any(host.endswith(h) or h in host for h in _IMG_SRC_HOSTS)


def _escape_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _escape_attr(text: str) -> str:
    return _escape_text(text).replace('"', "&quot;")


def sanitize_description_html(html: str | None) -> str:
    if not html:
        return ""
    parser = _Sanitizer()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        # Fall back to stripped text
        return _escape_text(re.sub(r"<[^>]+>", "", html))
    return parser.result()


def extract_description_image_srcs(html: str | None) -> list[str]:
    if not html:
        return []
    parser = _Sanitizer()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        return []
    return list(dict.fromkeys(parser.image_srcs))


def enhance_description_with_images(
    description_html: str | None,
    product_image_urls: list[str],
    *,
    max_images: int = 6,
) -> dict[str, Any]:
    """Preserve existing safe images; append ordered product images if none.

    Returns enhanced HTML plus UX flags. Images should eventually be migrated
    Daraz-hosted URLs before CreateProduct.
    """
    sanitized = sanitize_description_html(description_html)
    existing = extract_description_image_srcs(sanitized)
    existing_norm = {_norm_url(u) for u in existing}

    selected: list[str] = []
    for url in product_image_urls:
        if not url or not _safe_img_src(url):
            continue
        key = _norm_url(url)
        if key in existing_norm:
            continue
        selected.append(url)
        if len(selected) >= max_images:
            break

    if existing:
        return {
            "html": sanitized,
            "had_existing_images": True,
            "appended_images": [],
            "enhancement": "preserved",
            "message": "Existing description images preserved",
        }

    if not selected:
        return {
            "html": sanitized,
            "had_existing_images": False,
            "appended_images": [],
            "enhancement": "none",
            "message": "No product images available to append",
        }

    imgs = "".join(
        f'<p><img src="{_escape_attr(u)}" alt="Product image" /></p>' for u in selected
    )
    block = f'<div data-multistore-desc-images="1">{imgs}</div>'
    html = f"{sanitized}{block}" if sanitized else block
    return {
        "html": html,
        "had_existing_images": False,
        "appended_images": selected,
        "enhancement": "appended",
        "message": "Product images will be included in description",
    }


def _norm_url(url: str) -> str:
    return url.strip().split("?")[0].lower()
