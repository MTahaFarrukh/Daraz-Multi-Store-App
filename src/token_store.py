"""Local JSON token storage for Phase 2 POC (development only)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import DATA_DIR, TOKENS_PATH


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_country_user_info(raw: Any) -> dict[str, Any]:
    """Extract seller_id and country from token response country_user_info."""
    country = ""
    seller_id = ""
    user_id: int | str | None = None

    if isinstance(raw, list) and raw:
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            entry_country = str(entry.get("country", "")).lower()
            if entry_country in ("", "pk") or not country:
                country = entry_country or country
                seller_id = str(entry.get("seller_id", seller_id))
                user_id = entry.get("user_id", user_id)
                if entry_country == "pk":
                    break
    return {"country": country, "seller_id": seller_id, "user_id": user_id}


def build_token_record(token_response: dict[str, Any]) -> dict[str, Any]:
    """Normalize Daraz /auth/token/create or refresh response for local storage."""
    now = _utc_now()
    expires_in = int(token_response.get("expires_in") or 0)
    refresh_expires_in = int(token_response.get("refresh_expires_in") or 0)
    country_info = _parse_country_user_info(token_response.get("country_user_info"))

    access_expires_at = (
        (now + timedelta(seconds=expires_in)).isoformat() if expires_in else None
    )
    refresh_expires_at = (
        (now + timedelta(seconds=refresh_expires_in)).isoformat()
        if refresh_expires_in
        else None
    )

    return {
        "access_token": token_response.get("access_token", ""),
        "refresh_token": token_response.get("refresh_token", ""),
        "expires_in": expires_in,
        "refresh_expires_in": refresh_expires_in,
        "access_token_expires_at": access_expires_at,
        "refresh_token_expires_at": refresh_expires_at,
        "account": token_response.get("account", ""),
        "account_platform": token_response.get("account_platform", ""),
        "country": token_response.get("country") or country_info.get("country", ""),
        "seller_id": country_info.get("seller_id", ""),
        "user_id": country_info.get("user_id"),
        "country_user_info": token_response.get("country_user_info", []),
        "authorized_at": now.isoformat(),
        "request_id": token_response.get("request_id"),
    }


def save_tokens(record: dict[str, Any], path: Path | None = None) -> Path:
    target = path or TOKENS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return target


def load_tokens(path: Path | None = None) -> dict[str, Any] | None:
    target = path or TOKENS_PATH
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def sanitize_store_view(record: dict[str, Any] | None) -> dict[str, Any]:
    """Public-safe store metadata — never includes tokens."""
    if not record:
        return {"connected": False}

    expires_at = record.get("access_token_expires_at")
    seconds_remaining: int | None = None
    if expires_at:
        try:
            expiry = datetime.fromisoformat(str(expires_at))
            seconds_remaining = max(0, int((expiry - _utc_now()).total_seconds()))
        except ValueError:
            seconds_remaining = None

    return {
        "connected": True,
        "account": record.get("account", ""),
        "seller_id": record.get("seller_id", ""),
        "country": record.get("country", ""),
        "access_token_expires_at": expires_at,
        "access_token_expires_in_seconds": seconds_remaining,
        "authorized_at": record.get("authorized_at"),
    }


def mask_secret(value: str, visible: int = 4) -> str:
    """Mask a secret for safe diagnostic output."""
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"
