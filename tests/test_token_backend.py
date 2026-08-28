"""Tests for optional PostgreSQL token backend."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from src.token_store import ENCRYPTED_PREFIX, build_token_record, list_stores, upsert_store


@pytest.fixture()
def token_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / ".token_key"
    key_path.write_text(key, encoding="utf-8")
    tokens_path = tmp_path / "tokens.json"
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKENS_PATH", tokens_path)
    monkeypatch.setattr("src.token_store.get_env", lambda name, default="": "")
    return tokens_path


def test_database_backend_persists_stores(token_paths: Path) -> None:
    storage: dict[str, str] = {}

    def fake_use_database() -> bool:
        return True

    def fake_db_save(key: str, value: str) -> None:
        storage[key] = value

    def fake_db_load(key: str) -> str | None:
        return storage.get(key)

    record = build_token_record(
        {
            "access_token": "a1",
            "refresh_token": "r1",
            "expires_in": 3600,
            "account": "shop@example.com",
            "country_user_info": [{"country": "pk", "seller_id": "42"}],
        }
    )

    with (
        patch("src.token_store.use_database", fake_use_database),
        patch("src.token_store.db_save", fake_db_save),
        patch("src.token_store.db_load", fake_db_load),
        patch("src.token_backend.ensure_schema", lambda: None),
    ):
        upsert_store(record)
        assert storage
        assert next(iter(storage.values())).startswith(ENCRYPTED_PREFIX)
        stores = list_stores()
        assert len(stores) == 1
        assert stores[0]["account"] == "shop@example.com"
