"""Multi-store local token storage with optional Fernet encryption at rest."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from src.config import DATA_DIR, TOKENS_PATH, get_env
from src.token_backend import TOKENS_KV_KEY, db_load, db_save, use_database

TOKEN_KEY_PATH = DATA_DIR / ".token_key"
ENCRYPTED_PREFIX = "DMST1:"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def make_store_id(account: str = "", seller_id: str = "") -> str:
    """Stable store id from account email or seller_id."""
    raw = (account or seller_id or "unknown").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return slug or "unknown"


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
    account = str(token_response.get("account", "") or "")
    seller_id = str(country_info.get("seller_id", "") or "")

    access_expires_at = (
        (now + timedelta(seconds=expires_in)).isoformat() if expires_in else None
    )
    refresh_expires_at = (
        (now + timedelta(seconds=refresh_expires_in)).isoformat()
        if refresh_expires_in
        else None
    )

    store_id = make_store_id(account, seller_id)
    return {
        "store_id": store_id,
        "store_name": account or seller_id or store_id,
        "access_token": token_response.get("access_token", ""),
        "refresh_token": token_response.get("refresh_token", ""),
        "expires_in": expires_in,
        "refresh_expires_in": refresh_expires_in,
        "access_token_expires_at": access_expires_at,
        "refresh_token_expires_at": refresh_expires_at,
        "account": account,
        "account_platform": token_response.get("account_platform", ""),
        "country": token_response.get("country") or country_info.get("country", ""),
        "seller_id": seller_id,
        "user_id": country_info.get("user_id"),
        "country_user_info": token_response.get("country_user_info", []),
        "authorized_at": now.isoformat(),
        "request_id": token_response.get("request_id"),
    }


def _get_fernet() -> Fernet:
    env_key = get_env("DARAZ_TOKEN_KEY")
    if env_key:
        return Fernet(env_key.encode("ascii"))
    TOKEN_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_KEY_PATH.exists():
        return Fernet(TOKEN_KEY_PATH.read_text(encoding="utf-8").strip().encode("ascii"))
    key = Fernet.generate_key()
    TOKEN_KEY_PATH.write_text(key.decode("ascii"), encoding="utf-8")
    return Fernet(key)


def _encrypt_payload(plain_json: str) -> str:
    token = _get_fernet().encrypt(plain_json.encode("utf-8")).decode("ascii")
    return ENCRYPTED_PREFIX + token


def _decrypt_payload(raw: str) -> str:
    text = raw.strip()
    if text.startswith(ENCRYPTED_PREFIX):
        encrypted = text[len(ENCRYPTED_PREFIX) :]
        try:
            return _get_fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError(
                "Cannot decrypt tokens.json — wrong DARAZ_TOKEN_KEY or data/.token_key"
            ) from exc
    return text


def _is_legacy_single_store(data: dict[str, Any]) -> bool:
    return "access_token" in data and "stores" not in data


def _ensure_store_identity(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    if not out.get("store_id"):
        out["store_id"] = make_store_id(
            str(out.get("account", "")), str(out.get("seller_id", ""))
        )
    if not out.get("store_name"):
        out["store_name"] = out.get("account") or out.get("seller_id") or out["store_id"]
    return out


def _normalize_store_file(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return ({stores: [...]}, migrated)."""
    if _is_legacy_single_store(data):
        store = _ensure_store_identity(data)
        return {"stores": [store]}, True
    stores = data.get("stores")
    if not isinstance(stores, list):
        return {"stores": []}, False
    normalized = [_ensure_store_identity(s) for s in stores if isinstance(s, dict)]
    return {"stores": normalized}, False


def load_store_file(path: Path | None = None) -> dict[str, Any]:
    """Load multi-store token file; migrates Phase 2 single-record shape."""
    target = path or TOKENS_PATH
    if use_database() and path is None:
        raw = db_load(TOKENS_KV_KEY)
        if raw is None:
            # One-time import from disk if DB empty but file exists (local → cloud).
            if target.exists():
                raw = target.read_text(encoding="utf-8")
                plain = _decrypt_payload(raw)
                data = json.loads(plain)
                if isinstance(data, dict):
                    normalized, _ = _normalize_store_file(data)
                    save_store_file(normalized)
                    return normalized
            return {"stores": []}
    elif not target.exists():
        return {"stores": []}
    else:
        raw = target.read_text(encoding="utf-8")

    plain = _decrypt_payload(raw)
    data = json.loads(plain)
    if not isinstance(data, dict):
        return {"stores": []}

    normalized, migrated = _normalize_store_file(data)
    if migrated or raw.strip().startswith("{") and not raw.strip().startswith(ENCRYPTED_PREFIX):
        # Persist migrated / re-encrypt plaintext Phase 2 file.
        save_store_file(normalized, path=target)
    return normalized


def save_store_file(data: dict[str, Any], path: Path | None = None) -> Path:
    target = path or TOKENS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"stores": data.get("stores", [])}
    plain = json.dumps(payload, indent=2)
    encrypted = _encrypt_payload(plain)
    if use_database() and path is None:
        db_save(TOKENS_KV_KEY, encrypted)
        return target
    target.write_text(encrypted, encoding="utf-8")
    return target


def list_stores(path: Path | None = None) -> list[dict[str, Any]]:
    return list(load_store_file(path).get("stores") or [])


def get_store(store_id: str, path: Path | None = None) -> dict[str, Any] | None:
    needle = store_id.strip().lower()
    for store in list_stores(path):
        if str(store.get("store_id", "")).lower() == needle:
            return store
        if str(store.get("account", "")).lower() == needle:
            return store
    return None


def get_primary_store(path: Path | None = None) -> dict[str, Any] | None:
    stores = list_stores(path)
    return stores[0] if stores else None


def upsert_store(record: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Insert or update a store matched by account, then seller_id, then store_id."""
    store = _ensure_store_identity(record)
    data = load_store_file(path)
    stores = list(data.get("stores") or [])

    match_idx: int | None = None
    account = str(store.get("account", "")).lower()
    seller_id = str(store.get("seller_id", "")).lower()
    store_id = str(store.get("store_id", "")).lower()

    for idx, existing in enumerate(stores):
        if account and str(existing.get("account", "")).lower() == account:
            match_idx = idx
            break
        if seller_id and str(existing.get("seller_id", "")).lower() == seller_id:
            match_idx = idx
            break
        if store_id and str(existing.get("store_id", "")).lower() == store_id:
            match_idx = idx
            break

    if match_idx is None:
        stores.append(store)
    else:
        # Keep prior store_id/name if new record lacks nicer identity.
        prior = stores[match_idx]
        if not store.get("store_name") and prior.get("store_name"):
            store["store_name"] = prior["store_name"]
        store["store_id"] = prior.get("store_id") or store["store_id"]
        stores[match_idx] = store

    save_store_file({"stores": stores}, path=path)
    return store


def save_tokens(record: dict[str, Any], path: Path | None = None) -> Path:
    """Backward-compatible: upsert a single store record."""
    upsert_store(record, path=path)
    return path or TOKENS_PATH


def load_tokens(path: Path | None = None) -> dict[str, Any] | None:
    """Backward-compatible: return primary store or None."""
    return get_primary_store(path)


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
        "store_id": record.get("store_id", ""),
        "store_name": record.get("store_name", ""),
        "account": record.get("account", ""),
        "seller_id": record.get("seller_id", ""),
        "country": record.get("country", ""),
        "access_token_expires_at": expires_at,
        "access_token_expires_in_seconds": seconds_remaining,
        "authorized_at": record.get("authorized_at"),
    }


def list_sanitized_stores(path: Path | None = None) -> list[dict[str, Any]]:
    return [sanitize_store_view(s) for s in list_stores(path)]


def access_token_expires_soon(
    record: dict[str, Any],
    *,
    within_minutes: int = 60,
) -> bool:
    expires_at = record.get("access_token_expires_at")
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return True
    return expiry <= _utc_now() + timedelta(minutes=within_minutes)


def mask_secret(value: str, visible: int = 4) -> str:
    """Mask a secret for safe diagnostic output."""
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"
