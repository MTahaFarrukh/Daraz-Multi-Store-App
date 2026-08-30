"""Human-friendly store labels (not raw OAuth emails)."""

from __future__ import annotations

import re
from typing import Any

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


def derive_default_display_name(
    *,
    account: str = "",
    seller_id: str = "",
    store_id: str = "",
) -> str:
    """Build a short default label from account email or seller id."""
    account = (account or "").strip()
    if account and "@" in account:
        local = account.split("@", 1)[0]
        label = re.sub(r"[._+-]+", " ", local).strip()
        return label.title() if label else account

    seller_id = (seller_id or "").strip()
    if seller_id:
        return f"Seller {seller_id}"

    slug = (store_id or "").replace("_", " ").strip()
    return slug.title() if slug else "Store"


def store_display_name(record: dict[str, Any]) -> str:
    """Resolved label for UI and order tables."""
    custom = str(record.get("display_name") or "").strip()
    if custom:
        return custom

    stored = str(record.get("store_name") or "").strip()
    if stored and not looks_like_email(stored):
        return stored

    return derive_default_display_name(
        account=str(record.get("account") or ""),
        seller_id=str(record.get("seller_id") or ""),
        store_id=str(record.get("store_id") or ""),
    )
