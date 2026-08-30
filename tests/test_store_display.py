"""Tests for friendly store display names."""

from __future__ import annotations

from src.store_display import derive_default_display_name, store_display_name


def test_derive_from_email_local_part() -> None:
    name = derive_default_display_name(
        account="mtfdigitalemporiumofficial@gmail.com",
        store_id="mtfdigitalemporiumofficial_gmail_com",
    )
    assert name == "Mtfdigitalemporiumofficial"
    assert "@" not in name


def test_custom_display_name_wins() -> None:
    record = {
        "store_id": "a",
        "account": "vendor@shop.com",
        "store_name": "vendor@shop.com",
        "display_name": "MTF Main",
    }
    assert store_display_name(record) == "MTF Main"


def test_email_store_name_falls_back_to_derived() -> None:
    record = {
        "store_id": "a",
        "account": "ali.khan@daraz.pk",
        "store_name": "ali.khan@daraz.pk",
    }
    assert store_display_name(record) == "Ali Khan"
