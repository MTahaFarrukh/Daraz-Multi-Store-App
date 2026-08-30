"""
Daraz Multi-Store — FastAPI app (OAuth + dashboard API + UI).

Run:
  uvicorn src.app:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
from src.print_job import (
    begin_print_job,
    complete_print_job,
    fail_print_job,
    get_print_job_state,
    progress_callback,
    reset_print_job_if_stale,
)
from src.smoke_test import run_live_smoke_test
from src.token_refresh import refresh_store_tokens
from src.token_store import (
    build_token_record,
    list_sanitized_stores,
    sanitize_store_view,
    update_store_display_name,
    upsert_store,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _parse_store_ids(
    store: str | None,
    stores: str | None,
) -> tuple[str | None, list[str] | None]:
    """Resolve ?store= vs ?stores=id1,id2 (multi-select from dashboard)."""
    if stores is not None:
        ids = [part.strip() for part in stores.split(",") if part.strip()]
        if not ids:
            raise ValueError("No stores selected. Pick at least one store.")
        return None, ids
    return store, None

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
    """List connected stores; silently refresh tokens that expire soon."""
    try:
        refresh_store_tokens(within_minutes=60 * 24)
    except Exception as exc:
        logger.warning("Auto token refresh skipped: %s", exc)
    return {"stores": list_sanitized_stores()}


class RenameStoreBody(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)


@app.patch("/api/stores/{store_id}")
def api_rename_store(store_id: str, body: RenameStoreBody) -> dict:
    """Set a friendly store name (shown in UI instead of the OAuth email)."""
    try:
        record = update_store_display_name(store_id, body.display_name)
        return {"store": sanitize_store_view(record)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/stores")
def stores() -> dict:
    """Backward-compatible alias."""
    return api_stores()


@app.post("/api/refresh-tokens")
def api_refresh_tokens(
    store: str | None = Query(None),
    stores: str | None = Query(None, description="Comma-separated store_id list"),
    force: bool = Query(False),
) -> dict:
    try:
        store_id, store_ids = _parse_store_ids(store, stores)
        results = refresh_store_tokens(store_id=store_id, store_ids=store_ids, force=force)
        return {"results": results}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders")
def api_orders(
    store: str | None = Query(None),
    stores: str | None = Query(None, description="Comma-separated store_id list"),
    status: str = Query("ready_to_ship"),
    limit: int = Query(10, ge=1, le=50),
    created_after: str | None = Query(None),
) -> dict:
    try:
        store_id, store_ids = _parse_store_ids(store, stores)
        orders = fetch_orders(
            store_id=store_id,
            store_ids=store_ids,
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
    background_tasks: BackgroundTasks,
    store: str | None = Query(None),
    stores: str | None = Query(None, description="Comma-separated store_id list"),
    status: str = Query("ready_to_ship"),
    limit: int = Query(10, ge=1, le=30),
    created_after: str | None = Query(None),
    reuse_saved: bool = Query(False),
    wait: bool = Query(False, description="Block until done (local dev only)"),
) -> dict:
    reset_print_job_if_stale()
    try:
        store_id, store_ids = _parse_store_ids(store, stores)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def run_job() -> None:
        try:
            result = print_labels(
                store_id=store_id,
                store_ids=store_ids,
                status=status,
                limit=limit,
                created_after=created_after,
                reuse_saved=reuse_saved,
                on_progress=progress_callback(),
            )
            complete_print_job(result)
        except ValueError as exc:
            fail_print_job(str(exc))
        except LabelProcessingError as exc:
            logger.exception("Label PDF merge failed")
            fail_print_job(str(exc))
        except DarazApiError as exc:
            logger.exception("Daraz API error during print")
            fail_print_job(str(exc))
        except Exception as exc:
            logger.exception("Unexpected print job failure")
            fail_print_job(f"{type(exc).__name__}: {exc}")

    if wait:
        try:
            return print_labels(
                store_id=store_id,
                store_ids=store_ids,
                status=status,
                limit=limit,
                created_after=created_after,
                reuse_saved=reuse_saved,
            )
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

    if not begin_print_job():
        raise HTTPException(status_code=409, detail="A print job is already running")

    background_tasks.add_task(run_job)
    return {
        "status": "processing",
        "poll_url": "/api/print-labels/status",
        "message": "Print job started",
    }


@app.get("/api/print-labels/status")
def api_print_labels_status() -> dict:
    reset_print_job_if_stale()
    state = get_print_job_state()
    if state["status"] == "done" and state.get("result"):
        return {**state, **state["result"]}
    return state


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
