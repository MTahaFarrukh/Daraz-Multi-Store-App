"""
Daraz Multi-Store — FastAPI app (OAuth + multi-tenant dashboard API + UI).

Run:
  uvicorn src.app:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from src.auth import (
    AuthUser,
    WorkspaceContext,
    allow_legacy_data_import,
    auth_configured,
    get_current_user,
    get_workspace_context,
    is_production,
)
from src.auth.oauth_state import build_oauth_state, parse_oauth_state
from src.config import (
    DEFAULT_API_BASE,
    DEFAULT_OAUTH_AUTHORIZE,
    DEFAULT_REDIRECT_URI,
    get_env,
    require_env,
)
from src.daraz_api import DarazApiError, DarazClient
from src.db import get_repo
from src.label_processor import LabelProcessingError
from src.ops import fetch_orders, print_labels, print_labels_for_orders
from src.print_job import (
    begin_print_job,
    complete_print_job,
    fail_print_job,
    get_print_job,
    job_pdf_path,
    progress_callback,
    require_workspace_job,
    reset_print_job_if_stale,
)
from src.print_safety import validate_print_targets
from src.token_refresh import refresh_store_tokens
from src.token_store import build_token_record, sanitize_store_view

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


def _workspace_store_fns(workspace_id: str):
    repo = get_repo()

    def get_one(sid: str):
        return repo.get_store(workspace_id, sid)

    def list_all():
        return repo.list_stores(workspace_id)

    return get_one, list_all


STATIC_DIR = Path(__file__).resolve().parent / "static"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
LEGACY_UI = get_env("SERVE_LEGACY_UI", "").lower() in {"1", "true", "yes"}

_docs_url = None if is_production() else "/docs"
_openapi_url = None if is_production() else "/openapi.json"

app = FastAPI(
    title="Daraz Multi-Store Manager",
    description="Multi-tenant multi-store orders and shipping label printing for Daraz Pakistan",
    version="0.5.0",
    docs_url=_docs_url,
    redoc_url=None if is_production() else "/redoc",
    openapi_url=_openapi_url,
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_FRONTEND_ASSETS_MOUNTED = False


def _mount_frontend_assets() -> None:
    global _FRONTEND_ASSETS_MOUNTED
    if _FRONTEND_ASSETS_MOUNTED:
        return
    assets = FRONTEND_DIST / "assets"
    if not assets.is_dir():
        return
    app.mount("/assets", StaticFiles(directory=str(assets)), name="frontend_assets")
    _FRONTEND_ASSETS_MOUNTED = True


_mount_frontend_assets()


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


@app.on_event("startup")
def _startup() -> None:
    _mount_frontend_assets()
    if not auth_configured():
        logger.warning(
            "SUPABASE_URL is not set — authenticated APIs will return 503 until configured "
            "(JWKS verification requires SUPABASE_URL)"
        )
    if get_env("DATABASE_URL"):
        try:
            from src.db.connection import ensure_saas_schema

            ensure_saas_schema()
        except Exception as exc:
            logger.error("Failed to ensure SaaS schema: %s", exc)


def _spa_index() -> FileResponse | HTMLResponse:
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(
            index,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    # Never fall back to legacy static dashboard in production — React is primary.
    if not is_production():
        legacy = STATIC_DIR / "index.html"
        if legacy.is_file():
            return FileResponse(
                legacy,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
    return HTMLResponse(
        "<p>Frontend build missing. Run <code>npm run build</code> in <code>frontend/</code>.</p>",
        status_code=503,
    )


@app.get("/", response_model=None)
def root():
    if LEGACY_UI:
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return FileResponse(
                index,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
    return _spa_index()


@app.get("/login", response_model=None)
@app.get("/signup", response_model=None)
@app.get("/app", response_model=None)
@app.get("/app/{path:path}", response_model=None)
def spa_routes(path: str = ""):
    """Serve the React SPA for client-side routes (deep-link refresh safe)."""
    _ = path
    return _spa_index()


# ---------------------------------------------------------------------------
# Auth / workspace bootstrap
# ---------------------------------------------------------------------------


@app.get("/api/public-config")
def api_public_config() -> dict:
    """Browser-safe config (publishable key only — never secret key)."""
    return {
        "supabase_url": get_env("SUPABASE_URL"),
        "supabase_publishable_key": get_env("SUPABASE_PUBLISHABLE_KEY"),
        "auth_required": True,
    }


@app.get("/api/me")
def api_me(ctx: WorkspaceContext = Depends(get_workspace_context)) -> dict:
    repo = get_repo()
    memberships = repo.list_memberships(ctx.user.id)
    return {
        "user": {"id": ctx.user.id, "email": ctx.user.email},
        "workspace": {
            "id": ctx.workspace_id,
            "role": ctx.role,
        },
        "memberships": memberships,
    }


@app.post("/api/bootstrap")
def api_bootstrap(user: AuthUser = Depends(get_current_user)) -> dict:
    """Ensure the user has a workspace (owner). Idempotent."""
    repo = get_repo()
    name = (user.email or "My workspace").split("@")[0] or "My workspace"
    result = repo.create_workspace_with_owner(user.id, f"{name}'s workspace")
    return result


class LegacyImportBody(BaseModel):
    confirm: bool = False


@app.post("/api/admin/import-legacy-stores")
def api_import_legacy_stores(
    body: LegacyImportBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """
    Controlled import of the legacy global token vault into the current workspace.

    Requires ALLOW_LEGACY_DATA_IMPORT=true. Defaults to disabled.
    """
    if not allow_legacy_data_import():
        raise HTTPException(
            status_code=403,
            detail="Legacy import disabled. Set ALLOW_LEGACY_DATA_IMPORT=true to enable.",
        )
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Pass confirm=true to run import")
    if ctx.role != "owner":
        raise HTTPException(status_code=403, detail="Only workspace owners can import")

    from src.legacy_import import import_legacy_into_workspace

    result = import_legacy_into_workspace(ctx.workspace_id)
    return result


# ---------------------------------------------------------------------------
# Daraz OAuth (workspace-bound via signed state)
# ---------------------------------------------------------------------------


@app.get("/api/oauth/start")
def api_oauth_start(ctx: WorkspaceContext = Depends(get_workspace_context)) -> dict:
    """Return a Daraz authorize URL bound to the current workspace."""
    app_key = require_env("DARAZ_APP_KEY")
    redirect_uri = get_env("DARAZ_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    authorize_base = get_env("DARAZ_OAUTH_AUTHORIZE", DEFAULT_OAUTH_AUTHORIZE)
    state = build_oauth_state(workspace_id=ctx.workspace_id, user_id=ctx.user.id)
    url = DarazClient.build_authorize_url(
        app_key,
        redirect_uri,
        authorize_base=authorize_base,
        force_auth=True,
        state=state,
    )
    logger.info("OAuth start workspace=%s…", ctx.workspace_id[:8])
    return {"authorize_url": url}


@app.get("/oauth/login")
def oauth_login() -> RedirectResponse:
    """Legacy entry — require using authenticated /api/oauth/start."""
    return RedirectResponse("/login?need_auth=1", status_code=302)


@app.get("/oauth/callback", response_model=None)
def oauth_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from Daraz"),
    state: str | None = Query(None),
):
    """Exchange authorization code and bind the store to the workspace in state."""
    try:
        bound = parse_oauth_state(state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_id = bound["workspace_id"]
    user_id = bound["user_id"]
    repo = get_repo()
    if not repo.get_membership(workspace_id, user_id):
        raise HTTPException(status_code=403, detail="OAuth user is not a workspace member")

    try:
        client = DarazClient(
            app_key=require_env("DARAZ_APP_KEY"),
            app_secret=require_env("DARAZ_APP_SECRET"),
            api_base=get_env("DARAZ_API_BASE", DEFAULT_API_BASE),
        )
        token_response = client.create_token_from_code(code)
        record = repo.upsert_store(workspace_id, build_token_record(token_response))
        logger.info(
            "OAuth success workspace=%s store_id=%s account=%s",
            workspace_id[:8],
            record.get("store_id", ""),
            record.get("account", ""),
        )
        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return {
                "status": "authorized",
                "message": "Store connected. Tokens saved (not shown).",
                "store": sanitize_store_view(record),
                "workspace_id": workspace_id,
            }
        store_id = record.get("store_id", "")
        return RedirectResponse(f"/app/stores?connected=1&store={store_id}", status_code=302)
    except DarazApiError as exc:
        logger.error("Token exchange failed code=%s request_id=%s", exc.code, exc.request_id)
        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/html" not in accept:
            raise _daraz_http_error(exc) from exc
        return RedirectResponse(
            "/app/stores?oauth_error=1&message=token_exchange_failed",
            status_code=302,
        )
    except ValueError as exc:
        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/html" not in accept:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return RedirectResponse(
            "/app/stores?oauth_error=1&message=oauth_failed",
            status_code=302,
        )


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


@app.get("/api/stores")
def api_stores(ctx: WorkspaceContext = Depends(get_workspace_context)) -> dict:
    """List connected stores for the workspace; auto-refresh tokens expiring within 24h."""
    try:
        refresh_store_tokens(workspace_id=ctx.workspace_id, within_minutes=60 * 24)
    except Exception as exc:
        logger.warning("Auto token refresh skipped: %s", exc)
    stores = get_repo().list_stores(ctx.workspace_id)
    return {"stores": [sanitize_store_view(s) for s in stores], "workspace_id": ctx.workspace_id}


class RenameStoreBody(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Store name cannot be empty")
        return name


@app.patch("/api/stores/{store_id}")
def api_rename_store(
    store_id: str,
    body: RenameStoreBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    try:
        record = get_repo().update_store_display_name(
            ctx.workspace_id, store_id, body.display_name
        )
        return {"store": sanitize_store_view(record)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/stores")
def stores(ctx: WorkspaceContext = Depends(get_workspace_context)) -> dict:
    return api_stores(ctx)


@app.post("/api/refresh-tokens")
def api_refresh_tokens(
    store: str | None = Query(None),
    stores: str | None = Query(None, description="Comma-separated store_id list"),
    force: bool = Query(False),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    try:
        store_id, store_ids = _parse_store_ids(store, stores)
        results = refresh_store_tokens(
            store_id=store_id,
            store_ids=store_ids,
            force=force,
            workspace_id=ctx.workspace_id,
        )
        return {"results": results}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Store groups
# ---------------------------------------------------------------------------


class StoreGroupBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    store_ids: list[str] = Field(default_factory=list)


class StoreGroupUpdateBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    store_ids: list[str] | None = None


@app.get("/api/store-groups")
def api_list_groups(ctx: WorkspaceContext = Depends(get_workspace_context)) -> dict:
    return {"groups": get_repo().list_groups(ctx.workspace_id)}


@app.post("/api/store-groups")
def api_create_group(
    body: StoreGroupBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    repo = get_repo()
    # Validate store IDs belong to workspace
    for sid in body.store_ids:
        if not repo.get_store(ctx.workspace_id, sid):
            raise HTTPException(status_code=400, detail=f"Unknown store: {sid}")
    try:
        group = repo.create_group(ctx.workspace_id, body.name, body.store_ids)
        return {"group": group}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/store-groups/{group_id}")
def api_update_group(
    group_id: str,
    body: StoreGroupUpdateBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    repo = get_repo()
    if body.store_ids is not None:
        for sid in body.store_ids:
            if not repo.get_store(ctx.workspace_id, sid):
                raise HTTPException(status_code=400, detail=f"Unknown store: {sid}")
    try:
        group = repo.update_group(
            ctx.workspace_id,
            group_id,
            name=body.name,
            store_ids=body.store_ids,
        )
        return {"group": group}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/store-groups/{group_id}")
def api_delete_group(
    group_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    try:
        get_repo().delete_group(ctx.workspace_id, group_id)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ImportBrowserProfilesBody(BaseModel):
    profiles: dict[str, list[str]] = Field(default_factory=dict)


@app.post("/api/store-groups/import-browser")
def api_import_browser_profiles(
    body: ImportBrowserProfilesBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """One-time import from browser localStorage multistore_vendor_v1 profiles."""
    repo = get_repo()
    imported = []
    skipped = []
    for name, store_ids in (body.profiles or {}).items():
        valid = [sid for sid in store_ids if repo.get_store(ctx.workspace_id, sid)]
        if not name or not valid:
            skipped.append(name)
            continue
        group = repo.create_group(ctx.workspace_id, name, valid)
        imported.append(group)
    return {"imported": imported, "skipped": skipped}


# ---------------------------------------------------------------------------
# Orders (Phase 3 — local DB list; live Daraz at /api/orders/live)
# ---------------------------------------------------------------------------


class OrderSyncBody(BaseModel):
    store_ids: list[str] | None = None
    days: int | None = Field(None, ge=1, le=90)
    update_after: str | None = None
    created_after: str | None = None
    status: str = "all"
    include_items: bool = True


class PrintOrdersBody(BaseModel):
    order_ids: list[str] = Field(..., min_length=1)
    allow_reprint: bool = False


class ValidatePrintBody(BaseModel):
    order_ids: list[str] = Field(..., min_length=1)


def _resolve_list_store_filters(
    workspace_id: str,
    *,
    store: str | None,
    stores: str | None,
    store_ids: str | None,
    group_id: str | None,
) -> dict[str, Any]:
    """Build list_orders store filters.

    Omitting stores/store_ids → all workspace stores (Unified Orders "All Stores").
    Explicit empty ``stores=`` or ``store_ids=`` → 400 (empty ≠ all).
    """
    repo = get_repo()
    filters: dict[str, Any] = {}

    if stores is not None:
        ids = [part.strip() for part in stores.split(",") if part.strip()]
        if not ids:
            raise ValueError("No stores selected. Pick at least one store.")
        for sid in ids:
            if not repo.get_store(workspace_id, sid) and not repo.get_store_by_uuid(
                workspace_id, sid
            ):
                raise ValueError(f"Unknown store: {sid}")
        filters["store_slugs"] = ids

    if store_ids is not None:
        ids = [part.strip() for part in store_ids.split(",") if part.strip()]
        if not ids:
            raise ValueError("No stores selected. Pick at least one store.")
        uuids: list[str] = []
        slugs: list[str] = []
        for sid in ids:
            by_uuid = repo.get_store_by_uuid(workspace_id, sid)
            by_slug = repo.get_store(workspace_id, sid)
            if by_uuid:
                uuids.append(str(by_uuid["id"]))
            elif by_slug:
                uuids.append(str(by_slug["id"]))
                slugs.append(str(by_slug["store_id"]))
            else:
                raise ValueError(f"Unknown store: {sid}")
        filters["store_uuids"] = uuids

    if store:
        found = repo.get_store(workspace_id, store) or repo.get_store_by_uuid(
            workspace_id, store
        )
        if not found:
            raise ValueError(f"Unknown store: {store}")
        filters["store_uuids"] = [str(found["id"])]

    if group_id:
        groups = repo.list_groups(workspace_id)
        group = next((g for g in groups if str(g.get("id")) == str(group_id)), None)
        if not group:
            raise ValueError("Group not found")
        member_slugs = list(group.get("store_ids") or [])
        if not member_slugs:
            # Empty group members ≠ all stores
            filters["store_uuids"] = []
        else:
            filters["store_slugs"] = member_slugs

    return filters


def _order_public_view(order: dict[str, Any], *, store_slug: str | None = None) -> dict[str, Any]:
    return {
        "id": order.get("id"),
        "store_id": order.get("store_id"),
        "store_slug": store_slug,
        "store_display_name": order.get("store_display_name"),
        "daraz_order_id": order.get("daraz_order_id"),
        "order_number": order.get("order_number"),
        "status_raw": order.get("status_raw"),
        "status_group": order.get("status_group"),
        "statuses": order.get("statuses"),
        "created_at_daraz": order.get("created_at_daraz"),
        "updated_at_daraz": order.get("updated_at_daraz"),
        "price": order.get("price"),
        "currency": order.get("currency"),
        "items_count": order.get("items_count"),
        "customer_first_name": order.get("customer_first_name"),
        "customer_last_name": order.get("customer_last_name"),
        "payment_method": order.get("payment_method"),
        "shipping_fee": order.get("shipping_fee"),
        "warehouse_code": order.get("warehouse_code"),
        "synced_at": order.get("synced_at"),
        "has_print": order.get("has_print"),
        "print_count": order.get("print_count"),
        "last_printed_at": order.get("last_printed_at"),
    }


@app.get("/api/orders")
def api_orders(
    store: str | None = Query(None),
    stores: str | None = Query(
        None,
        description="Comma-separated store slugs. Omit for all stores; empty string → 400.",
    ),
    store_ids: str | None = Query(
        None, description="Comma-separated store UUIDs or slugs"
    ),
    group_id: str | None = Query(None),
    status_group: str | None = Query(None),
    status: str | None = Query(None, description="Alias filter on status_raw"),
    search: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    print_state: str = Query("any", description="unprinted|printed|any"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: str = Query("created_at_daraz_desc"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """List orders from the local DB (does not call Daraz).

    Omitting ``stores`` / ``store_ids`` returns all workspace stores.
    An explicit empty selection (``stores=``) is rejected — empty ≠ all.
    """
    try:
        store_filters = _resolve_list_store_filters(
            ctx.workspace_id,
            store=store,
            stores=stores,
            store_ids=store_ids,
            group_id=group_id,
        )
        filters = {
            **store_filters,
            "status_group": status_group,
            "status_raw": status,
            "search": search,
            "date_from": date_from,
            "date_to": date_to,
            "print_state": print_state,
            "page": page,
            "page_size": page_size,
            "sort": sort,
        }
        result = get_repo().list_orders(ctx.workspace_id, filters)
        # Attach store slugs / display names for UI
        stores_list = get_repo().list_stores(ctx.workspace_id)
        uuid_to_store = {str(s["id"]): s for s in stores_list}
        items = []
        for o in result["items"]:
            st = uuid_to_store.get(str(o.get("store_id"))) or {}
            view = _order_public_view(
                {
                    **o,
                    "store_display_name": st.get("display_name")
                    or st.get("store_name")
                    or st.get("store_id"),
                },
                store_slug=st.get("store_id"),
            )
            items.append(view)
        return {
            "orders": items,
            "items": items,
            "count": result["total"],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders/live")
def api_orders_live(
    store: str | None = Query(None),
    stores: str | None = Query(None, description="Comma-separated store_id list"),
    status: str = Query("ready_to_ship"),
    limit: int = Query(10, ge=1, le=50),
    created_after: str | None = Query(None),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Live Daraz order fetch (legacy). Prefer POST /api/shipping/rts for Shipping."""
    try:
        store_id, store_ids = _parse_store_ids(store, stores)
        get_one, list_all = _workspace_store_fns(ctx.workspace_id)
        orders = fetch_orders(
            store_id=store_id,
            store_ids=store_ids,
            status=status,
            limit=limit,
            created_after=created_after,
            get_store_fn=get_one,
            list_stores_fn=list_all,
        )
        return {"orders": orders, "count": len(orders)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DarazApiError as exc:
        raise _daraz_http_error(exc) from exc


class ShippingRtsBody(BaseModel):
    store_ids: list[str] = Field(..., min_length=1)
    upsert_headers: bool = True


@app.post("/api/shipping/rts")
def api_shipping_rts(
    body: ShippingRtsBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Load CURRENT ready_to_ship from Daraz for selected stores (empty ≠ all).

    Live Daraz RTS is the membership source of truth. Print badges come from
    ``order_label_prints``. Does not require a prior full order sync.
    """
    from src.shipping_rts import load_shipping_rts

    try:
        return load_shipping_rts(
            ctx.workspace_id,
            store_ids=body.store_ids,
            upsert_headers=body.upsert_headers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders/status-counts")
def api_orders_status_counts(
    store_ids: str | None = Query(None),
    stores: str | None = Query(None),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    try:
        store_filters = _resolve_list_store_filters(
            ctx.workspace_id,
            store=None,
            stores=stores,
            store_ids=store_ids,
            group_id=None,
        )
        uuids = store_filters.get("store_uuids")
        if uuids is None and store_filters.get("store_slugs"):
            repo = get_repo()
            uuids = []
            for slug in store_filters["store_slugs"]:
                s = repo.get_store(ctx.workspace_id, slug)
                if s:
                    uuids.append(str(s["id"]))
        counts = get_repo().count_orders_by_status_group(ctx.workspace_id, uuids)
        return {"counts": counts}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders/{order_id}")
def api_order_detail(
    order_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    repo = get_repo()
    order = repo.get_order_by_id(ctx.workspace_id, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    items = repo.list_order_items(ctx.workspace_id, order_id)
    prints = repo.list_label_prints_for_orders(ctx.workspace_id, [order_id]).get(
        order_id, []
    )
    summary = repo.get_print_summary_for_order(ctx.workspace_id, order_id)
    store = repo.get_store_by_uuid(ctx.workspace_id, str(order["store_id"]))
    return {
        "order": _order_public_view(
            {**order, **summary},
            store_slug=(store or {}).get("store_id"),
        ),
        "items": [
            {
                "id": i.get("id"),
                "daraz_order_item_id": i.get("daraz_order_item_id"),
                "daraz_order_id": i.get("daraz_order_id"),
                "status_raw": i.get("status_raw"),
                "package_id": i.get("package_id"),
                "name": i.get("name"),
                "sku": i.get("sku"),
                "quantity": i.get("quantity"),
                "item_price": i.get("item_price"),
                "paid_price": i.get("paid_price"),
                "currency": i.get("currency"),
                "tracking_code": i.get("tracking_code"),
                "shipment_provider": i.get("shipment_provider"),
                "shipping_type": i.get("shipping_type"),
                "warehouse_code": i.get("warehouse_code"),
            }
            for i in items
        ],
        "print_history": [
            {
                "id": p.get("id"),
                "printed_at": p.get("printed_at"),
                "is_reprint": p.get("is_reprint"),
                "package_id": p.get("package_id"),
                "order_item_ids": p.get("order_item_ids"),
                "fetch_source": p.get("fetch_source"),
                "print_job_id": p.get("print_job_id"),
            }
            for p in prints
        ],
        "print_summary": summary,
    }


@app.post("/api/orders/sync")
def api_orders_sync(
    body: OrderSyncBody | None = None,
    store_ids: str | None = Query(None),
    days: int | None = Query(None, ge=1, le=90),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.order_sync import sync_workspace_orders

    payload = body or OrderSyncBody()
    sid_list = payload.store_ids
    if store_ids is not None:
        sid_list = [p.strip() for p in store_ids.split(",") if p.strip()]
        if not sid_list:
            raise HTTPException(
                status_code=400, detail="No stores selected. Pick at least one store."
            )
    try:
        result = sync_workspace_orders(
            ctx.workspace_id,
            store_ids=sid_list,
            days=payload.days if payload.days is not None else days,
            update_after=payload.update_after,
            created_after=payload.created_after,
            status=payload.status,
            include_items=payload.include_items,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Print labels (per-workspace / per-job isolation)
# ---------------------------------------------------------------------------


@app.get("/api/print-jobs")
def api_list_print_jobs(
    limit: int = Query(30, ge=1, le=100),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """List recent print jobs for the active workspace (no cross-tenant leakage)."""
    jobs = get_repo().list_print_jobs(ctx.workspace_id, limit=limit)
    safe = []
    for job in jobs:
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        safe.append(
            {
                "id": job.get("id"),
                "status": job.get("status"),
                "message": job.get("message") or "",
                "error": job.get("error"),
                "started_at": job.get("started_at"),
                "updated_at": job.get("updated_at"),
                "pages": result.get("pages"),
                "labels": result.get("labels"),
                "has_download": bool(job.get("output_path")) and job.get("status") == "done",
            }
        )
    return {"jobs": safe}


@app.post("/api/print-labels/validate")
def api_print_labels_validate(
    body: ValidatePrintBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    return validate_print_targets(ctx.workspace_id, body.order_ids)


@app.post("/api/print-labels/orders")
def api_print_labels_orders(
    body: PrintOrdersBody,
    background_tasks: BackgroundTasks,
    wait: bool = Query(False, description="Block until done (local dev only)"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    def run_print(job_id: str) -> dict[str, Any]:
        out_path = job_pdf_path(ctx.workspace_id, job_id)
        return print_labels_for_orders(
            ctx.workspace_id,
            ctx.user.id,
            body.order_ids,
            allow_reprint=body.allow_reprint,
            job_id=job_id,
            download_url=f"/api/print-labels/{job_id}/download",
            output=out_path,
            on_progress=progress_callback(job_id),
        )

    if wait:
        try:
            job_id = begin_print_job(workspace_id=ctx.workspace_id, user_id=ctx.user.id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            result = run_print(job_id)
            complete_print_job(job_id, result)
            return {"job_id": job_id, **result}
        except Exception as exc:
            fail_print_job(job_id, str(exc))
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if isinstance(exc, LabelProcessingError):
                raise HTTPException(
                    status_code=502,
                    detail={"error": "label_processing_error", "message": str(exc)},
                ) from exc
            if isinstance(exc, DarazApiError):
                raise _daraz_http_error(exc) from exc
            raise

    try:
        job_id = begin_print_job(workspace_id=ctx.workspace_id, user_id=ctx.user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def run_job() -> None:
        try:
            result = run_print(job_id)
            complete_print_job(job_id, result)
        except ValueError as exc:
            fail_print_job(job_id, str(exc))
        except LabelProcessingError as exc:
            logger.exception("Label PDF merge failed")
            fail_print_job(job_id, str(exc))
        except DarazApiError as exc:
            logger.exception("Daraz API error during print")
            fail_print_job(job_id, str(exc))
        except Exception as exc:
            logger.exception("Unexpected print job failure")
            fail_print_job(job_id, f"{type(exc).__name__}: {exc}")

    background_tasks.add_task(run_job)
    return {
        "status": "processing",
        "job_id": job_id,
        "poll_url": f"/api/print-labels/{job_id}/status",
        "download_url": f"/api/print-labels/{job_id}/download",
        "message": "Print job started",
    }


@app.post("/api/print-labels")
def api_print_labels(
    background_tasks: BackgroundTasks,
    store: str | None = Query(None),
    stores: str | None = Query(None, description="Comma-separated store_id list"),
    status: str = Query("ready_to_ship"),
    limit: int = Query(10, ge=1, le=30),
    created_after: str | None = Query(None),
    reuse_saved: bool = Query(False),
    allow_reprint: bool = Query(False),
    wait: bool = Query(False, description="Block until done (local dev only)"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    try:
        store_id, store_ids = _parse_store_ids(store, stores)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    get_one, list_all = _workspace_store_fns(ctx.workspace_id)

    def run_print(job_id: str) -> dict[str, Any]:
        out_path = job_pdf_path(ctx.workspace_id, job_id)
        return print_labels(
            store_id=store_id,
            store_ids=store_ids,
            status=status,
            limit=limit,
            created_after=created_after,
            reuse_saved=reuse_saved,
            allow_reprint=allow_reprint,
            workspace_id=ctx.workspace_id,
            output=out_path,
            on_progress=progress_callback(job_id),
            get_store_fn=get_one,
            list_stores_fn=list_all,
            download_url=f"/api/print-labels/{job_id}/download",
        )

    if wait:
        try:
            job_id = begin_print_job(workspace_id=ctx.workspace_id, user_id=ctx.user.id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            result = run_print(job_id)
            complete_print_job(job_id, result)
            return {"job_id": job_id, **result}
        except Exception as exc:
            fail_print_job(job_id, str(exc))
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if isinstance(exc, LabelProcessingError):
                raise HTTPException(
                    status_code=502,
                    detail={"error": "label_processing_error", "message": str(exc)},
                ) from exc
            if isinstance(exc, DarazApiError):
                raise _daraz_http_error(exc) from exc
            raise

    try:
        job_id = begin_print_job(workspace_id=ctx.workspace_id, user_id=ctx.user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def run_job() -> None:
        try:
            result = run_print(job_id)
            complete_print_job(job_id, result)
        except ValueError as exc:
            fail_print_job(job_id, str(exc))
        except LabelProcessingError as exc:
            logger.exception("Label PDF merge failed")
            fail_print_job(job_id, str(exc))
        except DarazApiError as exc:
            logger.exception("Daraz API error during print")
            fail_print_job(job_id, str(exc))
        except Exception as exc:
            logger.exception("Unexpected print job failure")
            fail_print_job(job_id, f"{type(exc).__name__}: {exc}")

    background_tasks.add_task(run_job)
    return {
        "status": "processing",
        "job_id": job_id,
        "poll_url": f"/api/print-labels/{job_id}/status",
        "download_url": f"/api/print-labels/{job_id}/download",
        "message": "Print job started",
    }


@app.get("/api/print-labels/{job_id}/status")
def api_print_labels_status(
    job_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    reset_print_job_if_stale(job_id)
    try:
        state = require_workspace_job(job_id, ctx.workspace_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if state["status"] == "done" and state.get("result"):
        return {**state, **state["result"]}
    return state


@app.get("/api/print-labels/{job_id}/download")
def api_print_labels_download(
    job_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> FileResponse:
    try:
        job = require_workspace_job(job_id, ctx.workspace_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    path = Path(job.get("output_path") or job_pdf_path(ctx.workspace_id, job_id))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No combined PDF yet. Print labels first.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="combined-labels.pdf",
    )


# ---------------------------------------------------------------------------
# Store performance (Phase 2.5B — workspace leaderboard)
# ---------------------------------------------------------------------------


@app.get("/api/store-performance/months")
def api_store_performance_months(
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.performance_time import current_marketplace_month, iter_recent_months

    stored = get_repo().list_performance_months(ctx.workspace_id)
    recent = [{"year": y, "month": m} for y, m in iter_recent_months(6)]
    # Merge unique, prefer stored + recent suggestions
    seen: set[tuple[int, int]] = set()
    months: list[dict[str, int]] = []
    for item in stored + recent:
        key = (int(item["year"]), int(item["month"]))
        if key in seen:
            continue
        seen.add(key)
        months.append({"year": key[0], "month": key[1]})
    months.sort(key=lambda x: (x["year"], x["month"]), reverse=True)
    cy, cm = current_marketplace_month()
    return {
        "months": months,
        "current": {"year": cy, "month": cm},
        "timezone": "UTC+08:00 (Daraz/Lazada API timestamp convention)",
    }


@app.get("/api/store-performance")
def api_store_performance(
    year: int | None = Query(None),
    month: int | None = Query(None),
    metric: str = Query("orders"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.performance_sync import (
        GROSS_SALES_ENABLED,
        leaderboard_safe_view,
        rank_performance_rows,
    )
    from src.performance_time import MARKETPLACE_TZ_LABEL, current_marketplace_month

    if year is None or month is None:
        year, month = current_marketplace_month()
    if month < 1 or month > 12 or year < 2000:
        raise HTTPException(status_code=400, detail="Invalid year/month")

    metric_norm = (metric or "orders").lower()
    if metric_norm == "gross_sales" and not GROSS_SALES_ENABLED:
        raise HTTPException(status_code=400, detail="Gross Sales ranking is not enabled")
    if metric_norm not in {"orders", "gross_sales"}:
        metric_norm = "orders"

    repo = get_repo()
    stores = {str(s.get("id")): s for s in repo.list_stores(ctx.workspace_id) if s.get("id")}
    rows = repo.list_store_performance(ctx.workspace_id, year, month)
    # Only include stores still in workspace
    rows = [r for r in rows if str(r.get("store_id")) in stores]
    ranked = rank_performance_rows(rows, metric=metric_norm)
    leaderboard = [
        leaderboard_safe_view(r, stores.get(str(r.get("store_id")))) for r in ranked
    ]

    synced_ats = [r.get("orders_synced_at") for r in rows if r.get("orders_synced_at")]
    last_synced = max(synced_ats) if synced_ats else None

    return {
        "year": year,
        "month": month,
        "metric": metric_norm,
        "timezone": MARKETPLACE_TZ_LABEL,
        "orders_definition": "all_minus_canceled",
        "gross_sales_enabled": GROSS_SALES_ENABLED,
        "last_synced_at": last_synced,
        "leaderboard": leaderboard,
        "workspace_id": ctx.workspace_id,
    }


@app.post("/api/store-performance/sync")
def api_store_performance_sync(
    year: int | None = Query(None),
    month: int | None = Query(None),
    include_gross_sales: bool = Query(True),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.performance_sync import GROSS_SALES_ENABLED, sync_workspace_month
    from src.performance_time import current_marketplace_month

    if year is None or month is None:
        year, month = current_marketplace_month()
    if month < 1 or month > 12 or year < 2000:
        raise HTTPException(status_code=400, detail="Invalid year/month")

    summary = sync_workspace_month(
        ctx.workspace_id,
        year=year,
        month=month,
        include_gross_sales=include_gross_sales and GROSS_SALES_ENABLED,
    )
    # Return refreshed leaderboard
    board = api_store_performance(year=year, month=month, metric="orders", ctx=ctx)
    return {**summary, "leaderboard": board["leaderboard"], "last_synced_at": board["last_synced_at"]}


# ---------------------------------------------------------------------------
# Products (Phase 4B — local hub + clone draft; CreateProduct gated)
# ---------------------------------------------------------------------------


class ProductSyncBody(BaseModel):
    store_ids: list[str] | None = None
    # Catalog-only by default (Phase 4D). Detail hydration is lazy.
    fetch_details: bool = False


class ProductDefaultsBody(BaseModel):
    default_package_weight: float | None = None
    default_package_length: float | None = None
    default_package_width: float | None = None
    default_package_height: float | None = None
    default_initial_quantity: int | None = Field(None, ge=0, le=100000)
    sku_prefix: str | None = Field(None, max_length=16)


class CloneDraftBody(BaseModel):
    destination_store_id: str = Field(..., min_length=1)


class ConnectedFetchBody(BaseModel):
    source_store_id: str = Field(..., min_length=1)
    daraz_item_id: str = Field(..., min_length=1)


class ConnectedCloneDraftBody(BaseModel):
    source_store_id: str = Field(..., min_length=1)
    daraz_item_id: str = Field(..., min_length=1)
    destination_store_id: str = Field(..., min_length=1)


class ImportUrlDraftBody(BaseModel):
    url: str = Field(..., min_length=8)
    destination_store_id: str = Field(..., min_length=1)


def _product_public_view(
    product: dict[str, Any],
    *,
    store: dict[str, Any] | None = None,
    variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from src.description_enhance import sanitize_description_html

    st = store or {}
    prices = [
        float(v["price"])
        for v in (variants or [])
        if v.get("price") is not None
    ]
    stocks = [
        int(v["quantity"])
        for v in (variants or [])
        if v.get("quantity") is not None
    ]
    raw_desc = product.get("description_en") or product.get("description") or ""
    raw_short = product.get("short_description_en") or product.get("short_description") or ""
    return {
        **product,
        "description_html_safe": sanitize_description_html(raw_desc),
        "short_description_html_safe": sanitize_description_html(raw_short),
        "store_slug": st.get("store_id"),
        "store_display_name": st.get("display_name")
        or st.get("store_name")
        or st.get("store_id"),
        "variant_count": len(variants)
        if variants is not None
        else product.get("variants_count") or product.get("variant_count"),
        "price_min": min(prices) if prices else None,
        "price_max": max(prices) if prices else None,
        "stock_total": sum(stocks) if stocks else None,
        "has_video": bool(product.get("video_ref")),
        "primary_image": (
            ((product.get("images_json") or [{}])[0] or {}).get("url")
            if isinstance(product.get("images_json"), list)
            and product.get("images_json")
            else None
        ),
    }


@app.get("/api/products")
def api_list_products(
    store: str | None = Query(None),
    stores: str | None = Query(None),
    store_ids: str | None = Query(None),
    group_id: str | None = Query(None),
    status: str | None = Query(None),
    category_id: int | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: str = Query("synced_at_desc"),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """List local products (no Daraz calls). Empty stores= is rejected."""
    try:
        store_filters = _resolve_list_store_filters(
            ctx.workspace_id,
            store=store,
            stores=stores,
            store_ids=store_ids,
            group_id=group_id,
        )
        filters = {
            **store_filters,
            "status": status,
            "category_id": category_id,
            "search": search,
            "page": page,
            "page_size": page_size,
            "sort": sort,
        }
        result = get_repo().list_daraz_products(ctx.workspace_id, filters)
        stores_list = get_repo().list_stores(ctx.workspace_id)
        by_uuid = {str(s["id"]): s for s in stores_list}
        items = [
            _product_public_view(p, store=by_uuid.get(str(p.get("store_id"))))
            for p in result["items"]
        ]
        return {
            "products": items,
            "items": items,
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/products/{product_id}")
def api_get_product(
    product_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    repo = get_repo()
    product = repo.get_daraz_product(ctx.workspace_id, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    variants = repo.list_daraz_product_variants(ctx.workspace_id, product_id)
    store = repo.get_store_by_uuid(ctx.workspace_id, str(product["store_id"]))
    return {
        "product": _product_public_view(product, store=store, variants=variants),
        "variants": variants,
    }


@app.post("/api/products/sync")
def api_products_sync(
    body: ProductSyncBody | None = None,
    store_ids: str | None = Query(None),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.product_sync import sync_workspace_products

    payload = body or ProductSyncBody()
    sid_list = payload.store_ids
    if store_ids is not None:
        sid_list = [p.strip() for p in store_ids.split(",") if p.strip()]
        if not sid_list:
            raise HTTPException(
                status_code=400, detail="No stores selected. Pick at least one store."
            )
    try:
        return sync_workspace_products(
            ctx.workspace_id,
            store_ids=sid_list,
            fetch_details=payload.fetch_details,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/product-defaults")
def api_get_product_defaults(
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    return {"defaults": get_repo().get_product_defaults(ctx.workspace_id)}


@app.put("/api/product-defaults")
def api_put_product_defaults(
    body: ProductDefaultsBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    payload = body.model_dump(exclude_none=True)
    defaults = get_repo().upsert_product_defaults(ctx.workspace_id, payload)
    return {"defaults": defaults}


@app.post("/api/products/{product_id}/clone-draft")
def api_product_clone_draft(
    product_id: str,
    body: CloneDraftBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.product_clone import build_connected_clone_draft
    from src.product_fetch import ProductFetchError, ensure_product_detail

    try:
        try:
            ensure_product_detail(ctx.workspace_id, product_id)
        except ProductFetchError:
            # Fall back to local warehouse row when live detail hydrate is unavailable
            pass
        return build_connected_clone_draft(
            ctx.workspace_id,
            source_product_id=product_id,
            destination_store_id=body.destination_store_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/products/fetch-connected")
def api_fetch_connected_product(
    body: ConnectedFetchBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.product_fetch import ProductFetchError, fetch_connected_product

    try:
        return fetch_connected_product(
            ctx.workspace_id,
            source_store_id=body.source_store_id,
            daraz_item_id=body.daraz_item_id,
        )
    except ProductFetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/products/clone-draft/from-connected")
def api_clone_draft_from_connected(
    body: ConnectedCloneDraftBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.product_fetch import ProductFetchError, clone_draft_from_connected

    try:
        return clone_draft_from_connected(
            ctx.workspace_id,
            source_store_id=body.source_store_id,
            daraz_item_id=body.daraz_item_id,
            destination_store_id=body.destination_store_id,
        )
    except ProductFetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/products/{product_id}/ensure-detail")
def api_ensure_product_detail(
    product_id: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.product_fetch import ProductFetchError, ensure_product_detail

    try:
        return ensure_product_detail(ctx.workspace_id, product_id)
    except ProductFetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/products/import-url/draft")
def api_import_url_draft(
    body: ImportUrlDraftBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    from src.product_fetch import ProductFetchError
    from src.public_daraz import PublicDarazError, build_public_clone_draft

    try:
        return build_public_clone_draft(
            ctx.workspace_id,
            url=body.url,
            destination_store_id=body.destination_store_id,
        )
    except (PublicDarazError, ProductFetchError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/products/{product_id}/create-payload-preview")
def api_product_create_payload_preview(
    product_id: str,
    body: CloneDraftBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Build redacted CreateProduct preview. Never submits to Daraz."""
    from src.product_clone import build_connected_clone_draft
    from src.product_create_payload import build_create_product_payload_preview
    from src.product_fetch import ProductFetchError, ensure_product_detail

    try:
        ensure_product_detail(ctx.workspace_id, product_id)
        result = build_connected_clone_draft(
            ctx.workspace_id,
            source_product_id=product_id,
            destination_store_id=body.destination_store_id,
        )
    except ProductFetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    draft = result["draft"]
    preview = result.get("preview") or build_create_product_payload_preview(draft)
    return {
        "preview": preview,
        "validation": draft.get("validation"),
        "brand_resolution": draft.get("brand_resolution"),
        "create_probe_enabled": get_env("ALLOW_PRODUCT_CREATE_PROBE", "").lower()
        in {"1", "true", "yes"},
    }


class CreateProbeBody(BaseModel):
    destination_store_id: str = Field(..., min_length=1)
    confirm: bool = False
    execute: bool = False
    allow_duplicates: bool = False


@app.post("/api/products/{product_id}/create-probe")
def api_product_create_probe_for_id(
    product_id: str,
    body: CreateProbeBody,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Supervised CreateProduct path. Blocked unless ALLOW_PRODUCT_CREATE_PROBE.

    Never runs on startup/tests by default. execute=true required to call Daraz.
    """
    from src.product_create import run_supervised_create

    result = run_supervised_create(
        ctx.workspace_id,
        source_product_id=product_id,
        destination_store_id=body.destination_store_id,
        confirm=body.confirm,
        allow_duplicates=body.allow_duplicates,
        execute=body.execute,
    )
    if result.get("status") == "BLOCKED":
        reason = str(result.get("reason") or "blocked")
        code = 403 if "ALLOW_PRODUCT_CREATE_PROBE" in reason else 400
        raise HTTPException(status_code=code, detail=reason)
    return result


# Legacy download paths — intentionally disabled (no unauthenticated PDF access).
@app.get("/api/download/combined-labels")
def download_combined_labels_legacy() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "detail": "Use GET /api/print-labels/{job_id}/download with authentication.",
        },
    )


@app.get("/api/download/combined-labels-html")
def download_combined_labels_html_legacy() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={"detail": "Legacy HTML download removed."},
    )


@app.get("/test/live")
def test_live() -> JSONResponse:
    if is_production() or get_env("ENABLE_LIVE_TEST", "").lower() not in {
        "1",
        "true",
        "yes",
    }:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    return JSONResponse(
        status_code=410,
        content={"detail": "Use authenticated CLI smoke tests instead."},
    )
