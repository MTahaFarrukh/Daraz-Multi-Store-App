"""Shared configuration for Phase 2 OAuth POC."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TOKENS_PATH = DATA_DIR / "tokens.json"
TEST_LABEL_PATH = DATA_DIR / "test-label"
PHASE2_REPORT_PATH = PROJECT_ROOT / "docs" / "PHASE2_LIVE_TEST.md"

DEFAULT_API_BASE = "https://api.daraz.pk/rest"
DEFAULT_OAUTH_AUTHORIZE = "https://api.daraz.pk/oauth/authorize"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8000/oauth/callback"


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def require_env(name: str) -> str:
    value = get_env(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value
