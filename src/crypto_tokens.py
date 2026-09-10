"""Encrypt / decrypt Daraz tokens at rest (Fernet)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from src.config import DATA_DIR, get_env

TOKEN_KEY_PATH = DATA_DIR / ".token_key"
ENCRYPTED_PREFIX = "DMST1:"


def get_fernet() -> Fernet:
    env_key = get_env("DARAZ_TOKEN_KEY")
    if env_key:
        return Fernet(env_key.encode("ascii"))
    TOKEN_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_KEY_PATH.exists():
        return Fernet(TOKEN_KEY_PATH.read_text(encoding="utf-8").strip().encode("ascii"))
    key = Fernet.generate_key()
    TOKEN_KEY_PATH.write_text(key.decode("ascii"), encoding="utf-8")
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith(ENCRYPTED_PREFIX):
        return value
    token = get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return ENCRYPTED_PREFIX + token


def decrypt_secret(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if not text.startswith(ENCRYPTED_PREFIX):
        return text
    encrypted = text[len(ENCRYPTED_PREFIX) :]
    try:
        return get_fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Cannot decrypt store token — wrong DARAZ_TOKEN_KEY or data/.token_key"
        ) from exc
