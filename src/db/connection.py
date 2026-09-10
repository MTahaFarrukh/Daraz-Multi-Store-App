"""Postgres connection helpers for the SaaS schema."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.config import get_env

logger = logging.getLogger(__name__)

_SCHEMA_APPLIED = False
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def database_url() -> str:
    return get_env("DATABASE_URL")


def use_database() -> bool:
    return bool(database_url())


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


@contextmanager
def connect() -> Iterator:
    import psycopg

    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    with psycopg.connect(normalize_database_url(url)) as conn:
        yield conn


def ensure_saas_schema() -> None:
    """Apply Phase 1A relational schema (idempotent)."""
    global _SCHEMA_APPLIED
    if _SCHEMA_APPLIED or not use_database():
        return
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect() as conn:
        conn.execute(sql)
        conn.commit()
    _SCHEMA_APPLIED = True
    logger.info("SaaS database schema ready")


def reset_schema_flag_for_tests() -> None:
    global _SCHEMA_APPLIED
    _SCHEMA_APPLIED = False
