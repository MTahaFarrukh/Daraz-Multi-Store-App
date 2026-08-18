"""
Phase 2 — FastAPI OAuth + live smoke test server.

Run:
  uvicorn src.app:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from src.config import (
    DEFAULT_API_BASE,
    DEFAULT_OAUTH_AUTHORIZE,
    DEFAULT_REDIRECT_URI,
    get_env,
    require_env,
)
from src.daraz_api import DarazApiError, DarazClient
from src.smoke_test import run_live_smoke_test
from src.token_store import build_token_record, load_tokens, sanitize_store_view, save_tokens

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Daraz Phase 2 OAuth POC",
    description="Live production verification — OAuth + read-only order/label test",
    version="0.2.0",
)


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return """
<!DOCTYPE html>
<html>
<head><title>Daraz Phase 2 POC</title></head>
<body>
  <h1>Daraz Multi-Store — Phase 2 OAuth POC</h1>
  <p>Live production verification for Daraz Pakistan (read-only + GetDocument).</p>
  <ul>
    <li><a href="/oauth/login">Connect seller store (OAuth)</a></li>
    <li><a href="/stores">View connected store (sanitized)</a></li>
    <li><a href="/test/live">Run live smoke test</a></li>
  </ul>
  <p><small>Does not modify orders. Does not call /order/pack or /order/rts.</small></p>
</body>
</html>
"""


@app.get("/oauth/login")
def oauth_login() -> RedirectResponse:
    """Redirect browser to Daraz PK OAuth authorize URL."""
    app_key = require_env("DARAZ_APP_KEY")
    redirect_uri = get_env("DARAZ_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    authorize_base = get_env("DARAZ_OAUTH_AUTHORIZE", DEFAULT_OAUTH_AUTHORIZE)

    url = DarazClient.build_authorize_url(
        app_key,
        redirect_uri,
        authorize_base=authorize_base,
        force_auth=True,
    )
    logger.info("Redirecting to Daraz OAuth (client_id=%s...)", app_key[:4])
    return RedirectResponse(url, status_code=302)


@app.get("/oauth/callback")
def oauth_callback(
    code: str = Query(..., description="Authorization code from Daraz"),
    state: str | None = Query(None),
) -> dict:
    """Exchange authorization code for tokens and persist locally."""
    _ = state  # reserved for future CSRF validation
    try:
        client = DarazClient(
            app_key=require_env("DARAZ_APP_KEY"),
            app_secret=require_env("DARAZ_APP_SECRET"),
            api_base=get_env("DARAZ_API_BASE", DEFAULT_API_BASE),
        )
        token_response = client.create_token_from_code(code)
        record = build_token_record(token_response)
        save_tokens(record)
        logger.info(
            "OAuth success account=%s seller_id=%s",
            record.get("account", ""),
            record.get("seller_id", ""),
        )
        return {
            "status": "authorized",
            "message": "Store connected. Tokens saved locally (not shown).",
            "store": sanitize_store_view(record),
            "next_step": "GET /test/live to run the production smoke test",
        }
    except DarazApiError as exc:
        logger.error("Token exchange failed code=%s request_id=%s", exc.code, exc.request_id)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "token_exchange_failed",
                "daraz_code": exc.code,
                "message": str(exc),
                "request_id": exc.request_id,
                "http_status": exc.http_status,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/stores")
def stores() -> dict:
    """Sanitized connected store metadata — never exposes tokens."""
    record = load_tokens()
    return {"stores": [sanitize_store_view(record)] if record else []}


@app.get("/test/live")
def test_live(
    created_after: str | None = Query(
        None,
        description="ISO8601 lower bound for GetOrders (default from POC_CREATED_AFTER env)",
    ),
) -> dict:
    """
    Run read-only production smoke test:
    GetOrders → GetOrderItems → GetDocument (shippingLabel).
    Writes docs/PHASE2_LIVE_TEST.md and saves label to data/test-label.*
    """
    try:
        result = run_live_smoke_test(created_after=created_after, write_report=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "oauth": result.verdict_oauth(),
        "orders": result.verdict_orders(),
        "order_items": result.verdict_items(),
        "shipping_label": result.verdict_label(),
        "label_decode": result.verdict_decode(),
        "ready_to_ship_count": result.ready_to_ship_count,
        "orders_preview": result.orders_preview,
        "tested_order_id": result.tested_order_id,
        "tested_order_item_ids": result.tested_order_item_ids,
        "mime_type": result.mime_type,
        "output_file": result.output_file,
        "report": "docs/PHASE2_LIVE_TEST.md",
        "notes": result.notes,
        "errors": result.document_errors,
    }
