"""
Daraz Multi-Store — FastAPI app (OAuth + dashboard API + UI).

Run:
  uvicorn src.app:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.config import (
    DEFAULT_API_BASE,
    DEFAULT_OAUTH_AUTHORIZE,
    DEFAULT_REDIRECT_URI,
    get_env,
    require_env,
)
from src.daraz_api import DarazApiError, DarazClient
from src.label_processor import OUTPUT_DIR, LabelProcessingError
from src.ops import fetch_orders, print_labels
from src.smoke_test import run_live_smoke_test
from src.token_refresh import refresh_store_tokens
from src.token_store import (
    build_token_record,
    list_sanitized_stores,
    sanitize_store_view,
    upsert_store,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Daraz Multi-Store Manager",
    description="Multi-store orders and shipping label printing for Daraz Pakistan",
    version="0.3.0",
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _daraz_http_error(exc: DarazApiError) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={
            "error": "daraz_api_error",
            "daraz_code": exc.code,
            "message": str(exc),
            "request_id": exc.request_id,
            "http_status": exc.http_status,
        },
    )


@app.get("/", response_model=None)
def root():
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return HTMLResponse("<p>UI missing. Open /docs for API.</p>")


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


@app.get("/oauth/callback", response_model=None)
def oauth_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from Daraz"),
    state: str | None = Query(None),
):
    """Exchange authorization code for tokens and redirect to the dashboard."""
    _ = state
    try:
        client = DarazClient(
            app_key=require_env("DARAZ_APP_KEY"),
            app_secret=require_env("DARAZ_APP_SECRET"),
            api_base=get_env("DARAZ_API_BASE", DEFAULT_API_BASE),
        )
        token_response = client.create_token_from_code(code)
        record = upsert_store(build_token_record(token_response))
        logger.info(
            "OAuth success store_id=%s account=%s",
            record.get("store_id", ""),
            record.get("account", ""),
        )
        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return {
                "status": "authorized",
                "message": "Store connected. Tokens saved locally (not shown).",
                "store": sanitize_store_view(record),
            }
        store_id = record.get("store_id", "")
        return RedirectResponse(f"/?connected=1&store={store_id}", status_code=302)
    except DarazApiError as exc:
        logger.error("Token exchange failed code=%s request_id=%s", exc.code, exc.request_id)
        raise _daraz_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/stores")
def api_stores() -> dict:
    return {"stores": list_sanitized_stores()}


@app.get("/stores")
def stores() -> dict:
    """Backward-compatible alias."""
    return api_stores()


@app.post("/api/refresh-tokens")
def api_refresh_tokens(
    store: str | None = Query(None),
    force: bool = Query(False),
) -> dict:
    try:
        results = refresh_store_tokens(store_id=store, force=force)
        return {"results": results}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders")
def api_orders(
    store: str | None = Query(None),
    status: str = Query("ready_to_ship"),
    limit: int = Query(10, ge=1, le=50),
    created_after: str | None = Query(None),
) -> dict:
    try:
        orders = fetch_orders(
            store_id=store,
            status=status,
            limit=limit,
            created_after=created_after,
        )
        return {"orders": orders, "count": len(orders)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DarazApiError as exc:
        raise _daraz_http_error(exc) from exc


@app.post("/api/print-labels")
def api_print_labels(
    store: str | None = Query(None),
    status: str = Query("ready_to_ship"),
    limit: int = Query(10, ge=1, le=30),
    created_after: str | None = Query(None),
    reuse_saved: bool = Query(False),
) -> dict:
    try:
        result = print_labels(
            store_id=store,
            status=status,
            limit=limit,
            created_after=created_after,
            reuse_saved=reuse_saved,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LabelProcessingError as exc:
        logger.exception("Label PDF merge failed")
        raise HTTPException(
            status_code=502,
            detail={"error": "label_processing_error", "message": str(exc)},
        ) from exc
    except DarazApiError as exc:
        raise _daraz_http_error(exc) from exc


@app.get("/api/download/combined-labels")
def download_combined_labels() -> FileResponse:
    path = OUTPUT_DIR / "combined-labels.pdf"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No combined PDF yet. Print labels first.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="combined-labels.pdf",
    )


@app.get("/api/download/combined-labels-html")
def download_combined_labels_html() -> FileResponse:
    path = OUTPUT_DIR / "combined-labels.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No combined HTML yet. Print labels first.")
    return FileResponse(
        path,
        media_type="text/html",
        filename="combined-labels.html",
    )


@app.get("/test/live")
def test_live(
    created_after: str | None = Query(None),
) -> dict:
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
