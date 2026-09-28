"""Signed OAuth state binding Daraz callback to a workspace."""

from __future__ import annotations

import json
import time
from typing import Any

from cryptography.fernet import InvalidToken

from src.crypto_tokens import get_fernet

STATE_PREFIX = "oauth2:"
STATE_TTL_SECONDS = 60 * 30


def build_oauth_state(*, workspace_id: str, user_id: str) -> str:
    payload = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "exp": int(time.time()) + STATE_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":"))
    return STATE_PREFIX + get_fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def parse_oauth_state(state: str | None) -> dict[str, Any]:
    if not state or not state.startswith(STATE_PREFIX):
        raise ValueError("Missing or invalid OAuth state")
    try:
        plain = get_fernet().decrypt(state[len(STATE_PREFIX):].encode("ascii"))
        data = json.loads(plain)
        if not isinstance(data, dict) or type(data.get("exp")) is not int:
            raise ValueError("Invalid state payload")
        if not all(isinstance(data.get(k), str) and data[k].strip()
                   for k in ("workspace_id", "user_id")):
            raise ValueError("Invalid state binding")
    except (InvalidToken, ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("Invalid OAuth state — start Connect store again") from exc
    if data["exp"] <= int(time.time()):
        raise ValueError("OAuth state expired — start Connect store again")
    workspace_id = str(data.get("workspace_id") or "").strip()
    user_id = str(data.get("user_id") or "").strip()
    if not workspace_id or not user_id:
        raise ValueError("OAuth state incomplete")
    return {"workspace_id": workspace_id, "user_id": user_id}
