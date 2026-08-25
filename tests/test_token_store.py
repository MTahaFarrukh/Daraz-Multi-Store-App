"""Tests for multi-store token storage and Phase 3 adapters."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from pypdf import PdfWriter

from src.label_adapter import document_from_daraz_response
from src.token_refresh import refresh_store_tokens
from src.token_store import (
    ENCRYPTED_PREFIX,
    build_token_record,
    get_store,
    list_sanitized_stores,
    list_stores,
    load_store_file,
    make_store_id,
    upsert_store,
)


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


def test_make_store_id() -> None:
    assert make_store_id("Seller@Email.com") == "seller_email_com"


def test_migrate_legacy_single_store(token_paths: Path) -> None:
    legacy = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "account": "a@example.com",
        "seller_id": "99",
        "expires_in": 3600,
    }
    token_paths.write_text(json.dumps(legacy), encoding="utf-8")
    data = load_store_file(token_paths)
    assert len(data["stores"]) == 1
    assert data["stores"][0]["account"] == "a@example.com"
    assert data["stores"][0]["store_id"]
    raw = token_paths.read_text(encoding="utf-8")
    assert raw.startswith(ENCRYPTED_PREFIX)


def test_upsert_by_account(token_paths: Path) -> None:
    first = build_token_record(
        {
            "access_token": "a1",
            "refresh_token": "r1",
            "expires_in": 100,
            "account": "s1@x.com",
            "country_user_info": [{"country": "pk", "seller_id": "1"}],
        }
    )
    upsert_store(first, path=token_paths)
    second = build_token_record(
        {
            "access_token": "a2",
            "refresh_token": "r2",
            "expires_in": 200,
            "account": "s1@x.com",
            "country_user_info": [{"country": "pk", "seller_id": "1"}],
        }
    )
    upsert_store(second, path=token_paths)
    stores = list_stores(token_paths)
    assert len(stores) == 1
    assert stores[0]["access_token"] == "a2"

    other = build_token_record(
        {
            "access_token": "b1",
            "refresh_token": "rb",
            "expires_in": 100,
            "account": "s2@x.com",
            "country_user_info": [{"country": "pk", "seller_id": "2"}],
        }
    )
    upsert_store(other, path=token_paths)
    assert len(list_stores(token_paths)) == 2
    views = list_sanitized_stores(token_paths)
    assert all("access_token" not in v for v in views)
    assert get_store("s2@x.com", path=token_paths) is not None


def test_refresh_persistence(token_paths: Path) -> None:
    store = build_token_record(
        {
            "access_token": "old-at",
            "refresh_token": "old-rt",
            "expires_in": 60,
            "refresh_expires_in": 3600,
            "account": "r@x.com",
            "country_user_info": [{"country": "pk", "seller_id": "7"}],
        }
    )
    upsert_store(store, path=token_paths)

    fake_response = {
        "access_token": "new-at",
        "refresh_token": "new-rt",
        "expires_in": 3600,
        "refresh_expires_in": 7200,
        "account": "r@x.com",
        "country_user_info": [{"country": "pk", "seller_id": "7"}],
    }

    with (
        patch("src.token_refresh.require_env", side_effect=lambda n: "x"),
        patch("src.token_refresh.get_env", side_effect=lambda n, d="": d),
        patch("src.token_refresh.DarazClient") as client_cls,
    ):
        instance = MagicMock()
        instance.refresh_token.return_value = fake_response
        client_cls.return_value = instance
        results = refresh_store_tokens(force=True)

    assert results[0]["status"] == "refreshed"
    updated = get_store("r@x.com", path=token_paths)
    assert updated is not None
    assert updated["access_token"] == "new-at"
    assert updated["refresh_token"] == "new-rt"


def test_label_adapter_html() -> None:
    html = b"<html><body>label</body></html>"
    encoded = base64.b64encode(html).decode("ascii")
    doc = document_from_daraz_response(
        {"data": {"document": {"mime_type": "text/html", "file": encoded}}},
        store_id="store_a",
        store_name="Store A",
        order_id="111",
        order_item_ids=["222"],
    )
    assert doc.is_html()
    assert doc.order_id == "111"
    assert doc.order_item_id == "222"


def test_label_adapter_pdf() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    pdf = buf.getvalue()
    encoded = base64.b64encode(pdf).decode("ascii")
    doc = document_from_daraz_response(
        {"data": {"document": {"mime_type": "application/pdf", "file": encoded}}},
        store_id="store_a",
        store_name="Store A",
        order_id="333",
        order_item_ids=["444"],
    )
    assert doc.is_pdf()
