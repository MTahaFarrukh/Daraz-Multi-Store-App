"""Centralized destination SellerSku generation (MTF- policy)."""

from __future__ import annotations

import re
from typing import Iterable

DEFAULT_SKU_PREFIX = "MTF-"
# Phase 4A observed live SKUs up to len 32 with spaces; keep a conservative max.
MAX_SELLER_SKU_LEN = 50
_SAFE_RE = re.compile(r"[^A-Z0-9]+")


def normalize_sku_token(value: str | None, *, max_len: int = 24) -> str:
    text = (value or "").upper().strip()
    text = _SAFE_RE.sub("-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if not text:
        return "X"
    return text[:max_len].strip("-") or "X"


def build_product_token(title: str | None, *, max_len: int = 18) -> str:
    words = [w for w in re.split(r"\s+", (title or "").strip()) if w]
    # Prefer first meaningful word(s)
    joined = "-".join(words[:3]) if words else "PRODUCT"
    return normalize_sku_token(joined, max_len=max_len)


def build_variant_token(sale_props: dict | None, *, max_len: int = 16) -> str:
    props = sale_props or {}
    if not props:
        return "DEFAULT"
    # Stable key order
    parts = []
    for key in sorted(props.keys()):
        parts.append(str(props[key]))
    return normalize_sku_token("-".join(parts), max_len=max_len)


def generate_seller_sku(
    *,
    title: str | None,
    sale_props: dict | None = None,
    prefix: str = DEFAULT_SKU_PREFIX,
    existing: Iterable[str] | None = None,
    index: int = 0,
) -> str:
    """Generate a unique destination SellerSku.

    Format: {PREFIX}{PRODUCT}-{VARIANT}[-N]
    Prefix defaults to MTF- and is designed to become workspace-configurable.
    """
    pref = (prefix or DEFAULT_SKU_PREFIX).upper()
    if not pref.endswith("-"):
        pref = pref + "-"
    product = build_product_token(title)
    variant = build_variant_token(sale_props)
    base = f"{pref}{product}-{variant}"
    if len(base) > MAX_SELLER_SKU_LEN:
        # Shrink tokens to fit
        overflow = len(base) - MAX_SELLER_SKU_LEN
        cut = max(4, 16 - overflow // 2)
        product = build_product_token(title, max_len=cut)
        variant = build_variant_token(sale_props, max_len=cut)
        base = f"{pref}{product}-{variant}"
        base = base[:MAX_SELLER_SKU_LEN]

    taken = {str(s).upper() for s in (existing or []) if s}
    candidate = base
    n = 2
    # Incorporate index for multi-variant same saleProp edge cases
    if index > 0:
        candidate = f"{base}-{index + 1}"[:MAX_SELLER_SKU_LEN]
    while candidate.upper() in taken:
        suffix = f"-{n}"
        candidate = (base[: MAX_SELLER_SKU_LEN - len(suffix)] + suffix)
        n += 1
        if n > 9999:
            raise ValueError("Unable to allocate unique SellerSku")
    return candidate
