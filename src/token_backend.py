"""Optional persistent token storage (PostgreSQL) for cloud deploys."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from src.config import get_env

logger = logging.getLogger(__name__)

TOKENS_KV_KEY = "stores_v1"
_SCHEMA_READY = False


def database_url() -> str:
    return get_env("DATABASE_URL")


def use_database() -> bool:
    return bool(database_url())


@contextmanager
def _connect():
    import psycopg

    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # Render/Heroku often use postgres:// — psycopg expects postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    with psycopg.connect(url) as conn:
        yield conn


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or not use_database():
        return
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daraz_app_kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.commit()
    _SCHEMA_READY = True
    logger.info("Token database schema ready")


def db_load(key: str) -> str | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM daraz_app_kv WHERE key = %s",
            (key,),
        ).fetchone()
    if not row:
        return None
    return str(row[0])


def db_save(key: str, value: str) -> None:
    ensure_schema()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO daraz_app_kv (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (key, value),
        )
        conn.commit()
