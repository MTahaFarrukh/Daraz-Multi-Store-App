"""Smoke tests for Phase 2.5A Daraz client extensions (no live network)."""

from __future__ import annotations

from src.daraz_api import DarazClient


def test_get_orders_optional_date_params_signed(monkeypatch):
    client = DarazClient(app_key="k", app_secret="s", access_token="t")
    captured: dict = {}

    def fake_request(api_path, *, business_params=None, **kwargs):
        captured["api_path"] = api_path
        captured["business_params"] = business_params
        return {"code": "0", "data": {"orders": [], "count": 0, "countTotal": 0}}

    monkeypatch.setattr(client, "_request", fake_request)
    client.get_orders(
        created_after="2026-09-01T00:00:00+05:00",
        created_before="2026-10-01T00:00:00+05:00",
        update_after=None,
        status="all",
        limit=50,
        offset=0,
    )
    assert captured["api_path"] == "/orders/get"
    assert captured["business_params"]["created_before"] == "2026-10-01T00:00:00+05:00"
    assert captured["business_params"]["status"] == "all"
    assert "update_after" not in captured["business_params"]


def test_finance_helpers_paths(monkeypatch):
    client = DarazClient(app_key="k", app_secret="s", access_token="t")
    paths: list[str] = []

    def fake_request(api_path, *, business_params=None, **kwargs):
        paths.append(api_path)
        return {"code": "0", "data": []}

    monkeypatch.setattr(client, "_request", fake_request)
    client.get_finance_transaction_details(start_time="2026-09-01", end_time="2026-09-30")
    client.get_payout_status(created_after="2026-09-01")
    assert paths == [
        "/finance/transaction/details/get",
        "/finance/payout/status/get",
    ]
