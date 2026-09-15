"""Map Daraz order statuses to Unified Orders status_group buckets."""

from __future__ import annotations

from typing import Any

STATUS_GROUPS = (
    "pending",
    "ready_to_ship",
    "shipped",
    "delivered",
    "canceled",
    "returned",
    "other",
)

# Case-insensitive token sets per group.
_GROUP_TOKENS: dict[str, frozenset[str]] = {
    "pending": frozenset(
        {
            "unpaid",
            "pending",
            "pending_payment",
            "payment_pending",
            "processing",
        }
    ),
    "ready_to_ship": frozenset(
        {
            "ready_to_ship",
            "ready to ship",
            "rts",
            "packed",
            "repacked",
        }
    ),
    "shipped": frozenset(
        {
            "shipped",
            "shipping",
            "in_transit",
            "in transit",
        }
    ),
    "delivered": frozenset(
        {
            "delivered",
            "confirmed",
            "completed",
        }
    ),
    "canceled": frozenset(
        {
            "canceled",
            "cancelled",
            "cancel",
        }
    ),
    "returned": frozenset(
        {
            "returned",
            "returned_to_seller",
            "return",
            "failed",
            "failed_delivery",
            "lost",
            "damaged",
        }
    ),
}

# When an order has mixed statuses, prefer terminal / later lifecycle groups.
_PRIORITY = (
    "canceled",
    "returned",
    "delivered",
    "shipped",
    "ready_to_ship",
    "pending",
    "other",
)


def _normalize_tokens(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text.lower()] if text else []
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for item in raw:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text.lower())
        return out
    text = str(raw).strip()
    return [text.lower()] if text else []


def _token_group(token: str) -> str:
    for group, tokens in _GROUP_TOKENS.items():
        if token in tokens:
            return group
    # Soft match for variants like "ready-to-ship"
    compact = token.replace("-", "_").replace(" ", "_")
    for group, tokens in _GROUP_TOKENS.items():
        if compact in tokens or token.replace("-", " ") in tokens:
            return group
    if "ready" in token and "ship" in token:
        return "ready_to_ship"
    if "cancel" in token:
        return "canceled"
    if "return" in token:
        return "returned"
    if "deliver" in token:
        return "delivered"
    if "ship" in token:
        return "shipped"
    return "other"


def map_status_group(raw: Any) -> str:
    """Map a Daraz status string or list to a status_group. Unknown → other."""
    tokens = _normalize_tokens(raw)
    if not tokens:
        return "other"
    matched = {_token_group(t) for t in tokens}
    for group in _PRIORITY:
        if group in matched:
            return group
    return "other"


def status_raw_text(raw: Any) -> str | None:
    """Serialize statuses for storage as status_raw TEXT."""
    tokens = _normalize_tokens(raw)
    if not tokens:
        return None
    # Preserve a readable form; keep original casing when string.
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, (list, tuple)):
        return ",".join(str(x).strip() for x in raw if x is not None and str(x).strip())
    return str(raw)
