"""
Phase 2 live production smoke test (read-only + GetDocument).

Does NOT call /order/pack or /order/rts. Does not modify orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import (
    DEFAULT_API_BASE,
    PHASE2_REPORT_PATH,
    PROJECT_ROOT,
    TEST_LABEL_PATH,
    get_env,
)
from src.daraz_api import DarazApiError, DarazClient
from src.token_store import load_tokens, sanitize_store_view

LABEL_ELIGIBLE_STATUSES = frozenset(
    {"packed", "ready_to_ship", "ready to ship", "rts", "shipped"}
)


@dataclass
class SmokeTestResult:
    oauth_connected: bool = False
    get_orders_ok: bool = False
    ready_to_ship_count: int = 0
    orders_preview: list[dict[str, Any]] = field(default_factory=list)
    get_order_items_ok: bool = False
    tested_order_id: str | None = None
    tested_order_item_ids: list[str] = field(default_factory=list)
    get_document_ok: bool = False
    http_status: int | None = None
    daraz_code: str | None = None
    daraz_message: str | None = None
    request_id: str | None = None
    mime_type: str | None = None
    file_decoded: bool = False
    output_file: str | None = None
    document_errors: list[dict[str, Any]] = field(default_factory=list)
    store: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def verdict_oauth(self) -> str:
        return "PASS" if self.oauth_connected else "FAIL"

    def verdict_orders(self) -> str:
        return "PASS" if self.get_orders_ok else "FAIL"

    def verdict_items(self) -> str:
        return "PASS" if self.get_order_items_ok else "FAIL"

    def verdict_label(self) -> str:
        return "PASS" if self.get_document_ok else "FAIL"

    def verdict_decode(self) -> str:
        return "PASS" if self.file_decoded else "FAIL"


def _client_from_tokens() -> tuple[DarazClient, dict[str, Any]]:
    tokens = load_tokens()
    if not tokens or not tokens.get("access_token"):
        raise ValueError(
            "No stored tokens. Complete OAuth first: GET /oauth/login"
        )
    client = DarazClient(
        app_key=get_env("DARAZ_APP_KEY"),
        app_secret=get_env("DARAZ_APP_SECRET"),
        access_token=tokens["access_token"],
        api_base=get_env("DARAZ_API_BASE", DEFAULT_API_BASE),
    )
    return client, tokens


def _is_label_eligible(item: dict[str, Any]) -> bool:
    status = str(item.get("status", "")).strip().lower()
    return status in LABEL_ELIGIBLE_STATUSES or "ready" in status or status == "packed"


def _order_preview(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": order.get("order_id"),
        "order_number": order.get("order_number"),
        "items_count": order.get("items_count"),
        "statuses": order.get("statuses"),
        "created_at": order.get("created_at"),
    }


def run_live_smoke_test(
    *,
    created_after: str | None = None,
    order_limit: int = 10,
    write_report: bool = True,
) -> SmokeTestResult:
    """Run read-only production smoke test and optionally write PHASE2_LIVE_TEST.md."""
    result = SmokeTestResult()
    tokens = load_tokens()
    result.store = sanitize_store_view(tokens)
    result.oauth_connected = bool(tokens and tokens.get("access_token"))

    if not result.oauth_connected:
        result.notes.append("OAuth not completed — visit /oauth/login first.")
        if write_report:
            write_phase2_report(result)
        return result

    client, _ = _client_from_tokens()
    created_after = created_after or get_env(
        "POC_CREATED_AFTER", "2026-01-01T00:00:00+05:00"
    )

    # --- GetOrders ---
    try:
        orders_resp = client.get_orders(
            created_after=created_after,
            status="ready_to_ship",
            limit=order_limit,
            offset=0,
        )
        result.get_orders_ok = True
        orders = (orders_resp.get("data") or {}).get("orders") or []
        result.ready_to_ship_count = len(orders)
        result.orders_preview = [_order_preview(o) for o in orders[:5]]
        result.request_id = orders_resp.get("request_id") or result.request_id
    except DarazApiError as exc:
        result.daraz_code = exc.code
        result.daraz_message = str(exc)
        result.http_status = exc.http_status
        result.request_id = exc.request_id
        result.notes.append(f"GetOrders failed: [{exc.code}] {exc}")
        if write_report:
            write_phase2_report(result)
        return result

    if not orders:
        result.notes.append(
            "No ready_to_ship orders found. Cannot test GetDocument without eligible orders."
        )
        if write_report:
            write_phase2_report(result)
        return result

    # --- Try GetOrderItems + GetDocument on eligible items (no order mutation) ---
    for order in orders:
        order_id = order.get("order_id")
        if not order_id:
            continue
        try:
            items_resp = client.get_order_items(order_id)
            result.get_order_items_ok = True
            items = items_resp.get("data") or []
            eligible = [i for i in items if _is_label_eligible(i)]
            if not eligible:
                result.notes.append(
                    f"Order {order_id}: no label-eligible items (statuses: "
                    f"{[i.get('status') for i in items]}). Skipping."
                )
                continue

            item_ids = [str(i["order_item_id"]) for i in eligible if i.get("order_item_id")]
            if not item_ids:
                continue

            result.tested_order_id = str(order_id)
            result.tested_order_item_ids = item_ids

            try:
                doc_resp = client.get_shipping_label(item_ids)
                result.get_document_ok = True
                result.request_id = doc_resp.get("request_id") or result.request_id
                document = (doc_resp.get("data") or {}).get("document") or {}
                result.mime_type = document.get("mime_type") or document.get("MimeType")

                out_path = DarazClient.save_document(document, TEST_LABEL_PATH)
                result.file_decoded = out_path.exists() and out_path.stat().st_size > 0
                result.output_file = str(out_path.relative_to(PROJECT_ROOT))
                result.notes.append(
                    f"GetDocument succeeded for order {order_id}, items {item_ids}."
                )
                break
            except DarazApiError as exc:
                result.document_errors.append(
                    {
                        "order_id": str(order_id),
                        "order_item_ids": item_ids,
                        "http_status": exc.http_status,
                        "daraz_code": exc.code,
                        "message": str(exc),
                        "request_id": exc.request_id,
                    }
                )
                result.http_status = exc.http_status
                result.daraz_code = exc.code
                result.daraz_message = str(exc)
                result.request_id = exc.request_id
                result.notes.append(
                    f"GetDocument failed for order {order_id}: [{exc.code}] {exc}"
                )
                continue
        except DarazApiError as exc:
            result.daraz_code = exc.code
            result.daraz_message = str(exc)
            result.http_status = exc.http_status
            result.request_id = exc.request_id
            result.notes.append(f"GetOrderItems failed for {order_id}: [{exc.code}] {exc}")
            continue

    if write_report:
        write_phase2_report(result)
    return result


def write_phase2_report(result: SmokeTestResult) -> Path:
    """Write docs/PHASE2_LIVE_TEST.md from smoke test results."""
    store = result.store
    test_date = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    api_base = get_env("DARAZ_API_BASE", DEFAULT_API_BASE)

    label_verification = ""
    if result.file_decoded and result.output_file:
        ext = Path(result.output_file).suffix.lower()
        doc_kind = "PDF" if ext == ".pdf" else "HTML" if ext == ".html" else "binary"
        label_verification = f"""If a document was successfully retrieved:

- Output saved to `{result.output_file}`
- MIME type reported: `{result.mime_type or "unknown"}`
- Detected format: **{doc_kind}**
- Open the file locally to confirm it renders (browser for HTML, PDF viewer for PDF).
- Check for recognizable AWB/tracking/barcode content typical of Daraz shipping labels.
- **Do not assume Seller Center parity** until manually compared.

> **USER MUST NOW COMPARE `{result.output_file}` WITH THE LABEL GENERATED BY SELLER CENTER FOR THE SAME ORDER ITEM** (`{", ".join(result.tested_order_item_ids) or "n/a"}` on order `{result.tested_order_id or "n/a"}`).
"""
    elif result.get_document_ok:
        label_verification = "GetDocument returned success but file decoding/saving failed."
    else:
        label_verification = "No label document was retrieved."

    orders_section = ""
    if result.orders_preview:
        lines = []
        for o in result.orders_preview:
            lines.append(
                f"- order_id={o.get('order_id')} order_number={o.get('order_number')} "
                f"items_count={o.get('items_count')} statuses={o.get('statuses')}"
            )
        orders_section = "\n".join(lines)
    else:
        orders_section = "_No orders returned in preview._"

    errors_section = ""
    if result.document_errors:
        err_lines = []
        for err in result.document_errors:
            err_lines.append(
                f"- order `{err.get('order_id')}` items `{err.get('order_item_ids')}`: "
                f"HTTP {err.get('http_status')} code `{err.get('daraz_code')}` — "
                f"{err.get('message')} (request_id: {err.get('request_id')})"
            )
        errors_section = "\n\n### GetDocument attempts that failed\n\n" + "\n".join(err_lines)

    notes_section = ""
    if result.notes:
        notes_section = "\n\n### Notes\n\n" + "\n".join(f"- {n}" for n in result.notes)

    content = f"""# Phase 2 Live Daraz Pakistan Test

## Environment

* API base: `{api_base}`
* Account: `{store.get("account", "not connected")}`
* Seller ID: `{store.get("seller_id", "n/a")}`
* Test date: {test_date}

Do NOT put credentials/tokens in this file.

## OAuth

* Authorization successful: {"YES" if result.oauth_connected else "NO"}
* Token exchange successful: {"YES" if result.oauth_connected else "NO"}
* Token metadata retrieved: {"YES" if result.oauth_connected and store.get("seller_id") else "NO" if result.oauth_connected else "NO"}

## Orders

* GetOrders successful: {"YES" if result.get_orders_ok else "NO"}
* Ready-to-ship orders found: {result.ready_to_ship_count}
* GetOrderItems successful: {"YES" if result.get_order_items_ok else "NO"}

### Order preview (no customer PII)

{orders_section}

* Tested order_id: `{result.tested_order_id or "n/a"}`
* Tested order_item_ids: `{", ".join(result.tested_order_item_ids) or "n/a"}`

## Shipping Label

* GetDocument successful: {"YES" if result.get_document_ok else "NO"}
* HTTP status: `{result.http_status if result.http_status is not None else "n/a"}`
* Daraz code: `{result.daraz_code or "n/a"}`
* Daraz message: `{result.daraz_message or "n/a"}`
* request_id: `{result.request_id or "n/a"}`
* MIME type: `{result.mime_type or "n/a"}`
* File successfully decoded: {"YES" if result.file_decoded else "NO"}
* Output file: `{result.output_file or "n/a"}`
{errors_section}

## Label Verification

{label_verification}

## Critical next action

{"Complete OAuth at `/oauth/login` then re-run `/test/live`." if not result.oauth_connected else f"Compare `{result.output_file or 'data/test-label.*'}` with Seller Center for order item(s) `{', '.join(result.tested_order_item_ids) or 'n/a'}`." if result.file_decoded else "Resolve GetDocument eligibility or retry when ready_to_ship orders with packed/RTS items exist."}

Do not automatically declare the product fully validated.

## Final verdict

OAuth: {result.verdict_oauth()}
Order retrieval: {result.verdict_orders()}
Order-item retrieval: {result.verdict_items()}
Shipping-label retrieval: {result.verdict_label()}
Label document decoding: {result.verdict_decode()}
Seller Center parity: NOT YET TESTED
{notes_section}
"""

    PHASE2_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PHASE2_REPORT_PATH.write_text(content, encoding="utf-8")
    return PHASE2_REPORT_PATH


if __name__ == "__main__":
    outcome = run_live_smoke_test()
    print("Phase 2 smoke test complete.")
    print(f"  OAuth connected: {outcome.oauth_connected}")
    print(f"  Ready-to-ship orders: {outcome.ready_to_ship_count}")
    print(f"  GetDocument: {outcome.verdict_label()}")
    print(f"  Label saved: {outcome.output_file or 'none'}")
    print(f"  Report: docs/PHASE2_LIVE_TEST.md")
