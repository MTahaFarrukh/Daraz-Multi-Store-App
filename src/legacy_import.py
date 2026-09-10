"""Idempotent import of the legacy global token vault into a workspace."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import DATA_DIR, TOKENS_PATH, get_env
from src.db import get_repo
from src.token_backend import TOKENS_KV_KEY, db_load, use_database
from src.token_store import _decrypt_payload, _normalize_store_file, list_stores


def backup_legacy_vault(backup_dir: Path | None = None) -> Path | None:
    """Copy tokens.json (if present) to a timestamped backup. Never deletes source."""
    target = backup_dir or (DATA_DIR / "backups")
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backed: Path | None = None
    if TOKENS_PATH.exists():
        dest = target / f"tokens.{stamp}.json"
        shutil.copy2(TOKENS_PATH, dest)
        backed = dest
    if use_database():
        raw = db_load(TOKENS_KV_KEY)
        if raw:
            dest = target / f"stores_v1.{stamp}.txt"
            dest.write_text(raw, encoding="utf-8")
            backed = dest
    return backed


def load_legacy_stores() -> list[dict[str, Any]]:
    """Load stores from legacy vault without mutating tenant tables."""
    if use_database():
        raw = db_load(TOKENS_KV_KEY)
        if raw:
            plain = _decrypt_payload(raw)
            data = json.loads(plain)
            if isinstance(data, dict):
                normalized, _ = _normalize_store_file(data)
                return list(normalized.get("stores") or [])
    if TOKENS_PATH.exists():
        return list_stores(TOKENS_PATH)
    return []


def import_legacy_into_workspace(workspace_id: str) -> dict[str, Any]:
    """
    Import legacy stores into workspace_id.

    Idempotent: re-running upserts the same store_id rows (tokens/display preserved).
    Does NOT delete the legacy vault.
    """
    backup = backup_legacy_vault()
    stores = load_legacy_stores()
    repo = get_repo()
    imported = []
    for record in stores:
        saved = repo.upsert_store(workspace_id, record)
        imported.append(
            {
                "store_id": saved.get("store_id"),
                "account": saved.get("account"),
                "display_name": saved.get("display_name"),
            }
        )
    return {
        "workspace_id": workspace_id,
        "imported_count": len(imported),
        "stores": imported,
        "backup": str(backup) if backup else None,
        "legacy_vault_preserved": True,
        "allow_flag": get_env("ALLOW_LEGACY_DATA_IMPORT"),
    }
