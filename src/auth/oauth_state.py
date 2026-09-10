"""Signed OAuth state binding Daraz callback to a workspace."""

from __future__ import annotations

import json
import time
from typing import Any

from src.crypto_tokens import decrypt_secret, encrypt_secret

STATE_PREFIX = "oauth1:"
STATE_TTL_SECONDS = 60 * 30


def build_oauth_state(*, workspace_id: str, user_id: str) -> str:
    payload = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "exp": int(time.time()) + STATE_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":"))
    return STATE_PREFIX + encrypt_secret(raw)


def parse_oauth_state(state: str | None) -> dict[str, Any]:
    if not state or not state.startswith(STATE_PREFIX):
        raise ValueError("Missing or invalid OAuth state")
    encrypted = state[len(STATE_PREFIX) :]
    # encrypt_secret already adds DMST1:; build_oauth_state double-wraps via encrypt_secret
    # Actually build uses encrypt_secret which adds DMST1. So state is oauth1:DMST1:...
    plain = decrypt_secret(encrypted)
    data = json.loads(plain)
    if int(data.get("exp") or 0) < int(time.time()):
        raise ValueError("OAuth state expired — start Connect store again")
    workspace_id = str(data.get("workspace_id") or "").strip()
    user_id = str(data.get("user_id") or "").strip()
    if not workspace_id or not user_id:
        raise ValueError("OAuth state incomplete")
    return {"workspace_id": workspace_id, "user_id": user_id}
