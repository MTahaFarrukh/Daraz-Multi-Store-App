"""Production environment validation — fail closed before serving traffic."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_production_env() -> bool:
    env = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").strip().lower()
    if env in {"production", "prod"}:
        return True
    return _truthy(os.getenv("RENDER"))


def validate_concurrency_env() -> list[str]:
    """Return warnings for invalid concurrency knobs (non-fatal)."""
    warnings: list[str] = []
    for key, default, lo, hi in (
        ("PRODUCT_DESTINATION_CONCURRENCY", "3", 1, 16),
        ("PRINT_STORE_CONCURRENCY", "4", 1, 16),
        ("SHIPPING_RTS_CONCURRENCY", "6", 1, 32),
    ):
        raw = (os.getenv(key) or default).strip()
        try:
            n = int(raw)
            if n < lo or n > hi:
                warnings.append(f"{key}={raw} outside [{lo},{hi}] — using clamped value at call sites")
        except ValueError:
            warnings.append(f"{key}={raw!r} is not an integer")
    return warnings


def validate_production_environment() -> dict[str, Any]:
    """Raise RuntimeError when production config is unsafe.

    Never includes secret values in the exception message.
    """
    if not is_production_env():
        return {"ok": True, "production": False, "warnings": validate_concurrency_env()}

    errors: list[str] = []

    if not (os.getenv("DATABASE_URL") or "").strip():
        errors.append("DATABASE_URL is required in production")

    if not (os.getenv("DARAZ_TOKEN_KEY") or "").strip():
        errors.append("DARAZ_TOKEN_KEY is required in production (no ephemeral key generation)")

    if _truthy(os.getenv("AUTH_TEST_MODE")):
        errors.append("AUTH_TEST_MODE must not be enabled in production")

    tenancy = (os.getenv("TENANCY_REPO") or "").strip().lower()
    if tenancy == "memory":
        errors.append("TENANCY_REPO=memory is not allowed in production")

    if not (os.getenv("SUPABASE_URL") or "").strip():
        errors.append("SUPABASE_URL is required in production")

    warnings = validate_concurrency_env()
    if errors:
        # Do not interpolate env values — only keys / short codes.
        raise RuntimeError(
            "Production environment validation failed: " + "; ".join(errors)
        )
    return {"ok": True, "production": True, "warnings": warnings}
