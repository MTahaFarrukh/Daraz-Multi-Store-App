"""Classify Daraz CreateProduct / API detail codes into definite vs uncertain."""

from __future__ import annotations

import re
from typing import Any


# Definite rejection classes — never treat as “maybe succeeded”.
DEFINITE_BRAND_CODES = frozenset({"C011", "BRAND:C011", "BRAND_C011"})
DEFINITE_DUPLICATE_ATTR_CODES = frozenset({"C008", "ATTR:C008", "ATTRIBUTE:C008"})
DEFINITE_DUPLICATE_SKU_TOKENS = (
    "duplicate seller sku",
    "sellersku already exists",
    "seller_sku already exists",
    "sku already exist",
)


def _flatten_detail_text(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for key in ("message", "type", "code", "detail"):
        val = payload.get(key)
        if val is not None:
            parts.append(str(val))
    detail = payload.get("detail")
    if isinstance(detail, list):
        for item in detail:
            if isinstance(item, dict):
                for k in ("message", "code", "field", "msg"):
                    if item.get(k) is not None:
                        parts.append(str(item.get(k)))
            else:
                parts.append(str(item))
    elif isinstance(detail, dict):
        parts.append(str(detail))
    return " ".join(parts)


def extract_detail_codes(payload: dict[str, Any] | None, *, code: str | None = None) -> list[str]:
    """Pull detail codes such as Brand:C011 / C008 from payload text."""
    found: list[str] = []
    blob = _flatten_detail_text(payload)
    if code:
        blob = f"{code} {blob}"
    for match in re.finditer(
        r"\b(?:Brand|Attribute|Attr|SellerSku|SKU)?\s*:?\s*(C\d{3})\b",
        blob,
        flags=re.I,
    ):
        prefix = match.group(0)
        c = match.group(1).upper()
        if "brand" in prefix.lower():
            found.append(f"Brand:{c}")
        else:
            found.append(c)
    # Also catch bare C011 after Brand word nearby
    if re.search(r"brand.{0,24}C011|C011.{0,24}brand", blob, flags=re.I):
        if "Brand:C011" not in found:
            found.append("Brand:C011")
    # Dedupe preserve order
    out: list[str] = []
    for item in found:
        if item not in out:
            out.append(item)
    return out


def classify_daraz_create_error(
    *,
    code: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
    http_status: int | None = None,
) -> dict[str, Any]:
    """Return rejection classification for create / API errors.

    Definite classes block automatic corroboration. Timeouts / unknown stay uncertain.
    """
    blob = " ".join(
        p
        for p in (
            str(code or ""),
            str(message or ""),
            _flatten_detail_text(payload),
        )
        if p
    )
    low = blob.lower()
    detail_codes = extract_detail_codes(payload, code=code)

    if http_status == 408 or any(
        tok in low for tok in ("timeout", "timed out", "gateway time")
    ):
        return {
            "class": "UNCERTAIN_TIMEOUT",
            "definite": False,
            "blocks_corroboration": False,
            "detail_codes": detail_codes,
            "reason": "timeout_or_transport",
        }

    for dc in detail_codes:
        norm = dc.upper().replace(" ", "")
        if norm in {"BRAND:C011", "C011"} or "BRAND:C011" in dc.upper():
            return {
                "class": "DEFINITE_BRAND_REJECTION",
                "definite": True,
                "blocks_corroboration": True,
                "detail_codes": detail_codes,
                "reason": "brand_rejected",
            }
        if norm in {"C008", "ATTR:C008", "ATTRIBUTE:C008"} or dc.upper().endswith("C008"):
            return {
                "class": "DEFINITE_DUPLICATE_ATTRIBUTE",
                "definite": True,
                "blocks_corroboration": False,
                "detail_codes": detail_codes,
                "reason": "duplicate_attribute",
            }

    if any(tok in low for tok in DEFINITE_DUPLICATE_SKU_TOKENS) or (
        "sellersku" in low and "duplicate" in low
    ):
        return {
            "class": "DEFINITE_DUPLICATE_SELLER_SKU",
            "definite": True,
            "blocks_corroboration": False,
            "detail_codes": detail_codes,
            "reason": "duplicate_seller_sku",
        }

    if "brand" in low and any(c in blob.upper() for c in ("C011", "INVALID BRAND", "BRAND NOT")):
        return {
            "class": "DEFINITE_BRAND_REJECTION",
            "definite": True,
            "blocks_corroboration": True,
            "detail_codes": detail_codes or ["Brand:C011"],
            "reason": "brand_rejected",
        }

    return {
        "class": "UNCERTAIN_UNKNOWN",
        "definite": False,
        "blocks_corroboration": False,
        "detail_codes": detail_codes,
        "reason": "unknown_or_inconclusive",
    }
