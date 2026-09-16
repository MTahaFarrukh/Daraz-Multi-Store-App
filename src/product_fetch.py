"""Direct connected-store product fetch (one Item ID → one /product/item/get).

Copy Product must NOT sync the whole catalog. This module:
- validates workspace ownership of the source store
- calls Daraz ``/product/item/get`` for THAT item only
- normalizes + upserts into the local warehouse with detail_complete=True
- returns timing instrumentation
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from src.daraz_api import DarazApiError, DarazClient
from src.db.repo import get_repo
from src.ops import client_for_store
from src.product_sync import product_payload_from_daraz, variant_payloads_from_daraz
from src.token_refresh import refresh_one_store
from src.token_store import access_token_expires_soon

logger = logging.getLogger(__name__)

DETAIL_FRESH_HOURS = 24


class ProductFetchError(Exception):
    """User-facing direct-fetch failure (clear message, no silent fallback)."""

    def __init__(self, message: str, *, code: str = "fetch_failed"):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def is_detail_fresh(product: dict[str, Any] | None, *, hours: int = DETAIL_FRESH_HOURS) -> bool:
    if not product:
        return False
    if not product.get("detail_complete"):
        return False
    synced = _parse_ts(product.get("detail_synced_at"))
    if not synced:
        return False
    return synced >= _now() - timedelta(hours=hours)


def _resolve_store(workspace_id: str, store_ref: str) -> dict[str, Any]:
    repo = get_repo()
    store = repo.get_store(workspace_id, store_ref) or repo.get_store_by_uuid(
        workspace_id, store_ref
    )
    if not store:
        raise ProductFetchError(
            "Source store not found in this workspace",
            code="store_not_found",
        )
    return store


def _client_for(workspace_id: str, store: dict[str, Any]) -> DarazClient:
    repo = get_repo()
    slug = str(store.get("store_id") or "")
    try:

        def _upsert(record: dict[str, Any]) -> dict[str, Any]:
            return repo.upsert_store(workspace_id, record)

        if access_token_expires_soon(store, within_minutes=60):
            refresh_one_store(store, upsert_fn=_upsert)
            store = repo.get_store(workspace_id, slug) or store
    except Exception as exc:  # noqa: BLE001
        logger.info("Token refresh skipped for product fetch %s: %s", slug, exc)
    client = client_for_store(store)
    client.timeout = 45.0
    return client


def _upsert_detail_product(
    workspace_id: str,
    store_uuid: str,
    daraz_product: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    t0 = time.perf_counter()
    repo = get_repo()
    payload = product_payload_from_daraz(workspace_id, store_uuid, daraz_product)
    if not payload.get("daraz_item_id"):
        raise ProductFetchError(
            "Daraz returned a product without item_id",
            code="invalid_payload",
        )
    now = _now().isoformat()
    payload["catalog_seen_at"] = now
    payload["detail_synced_at"] = now
    payload["detail_complete"] = True
    product = repo.upsert_daraz_product(payload)
    variants = variant_payloads_from_daraz(
        workspace_id, store_uuid, str(product["id"]), daraz_product
    )
    saved = repo.replace_product_variants(workspace_id, str(product["id"]), variants)
    db_ms = (time.perf_counter() - t0) * 1000
    return product, saved, db_ms


def fetch_connected_product(
    workspace_id: str,
    *,
    source_store_id: str,
    daraz_item_id: str,
) -> dict[str, Any]:
    """Fetch ONE connected-store product by Item ID and upsert locally.

    Makes exactly one Daraz product API call: ``/product/item/get``.
    Does not call ``/products/get`` or hydrate unrelated items.
    """
    item_id = str(daraz_item_id or "").strip()
    if not item_id:
        raise ProductFetchError("Product / Item ID is required", code="missing_item_id")
    if not item_id.isdigit():
        raise ProductFetchError(
            "Item ID must be numeric (Daraz item_id)",
            code="invalid_item_id",
        )

    store = _resolve_store(workspace_id, source_store_id)
    store_uuid = str(store["id"])
    client = _client_for(workspace_id, store)

    t_api = time.perf_counter()
    try:
        resp = client.get_product_item(item_id)
    except DarazApiError as exc:
        msg = str(exc)
        code = str(exc.code or "")
        low = msg.lower()
        if code in {"404", "NotFound"} or "not found" in low or "does not exist" in low:
            raise ProductFetchError(
                f"Item {item_id} was not found on the selected store "
                f"({store.get('display_name') or store.get('store_id')})",
                code="item_not_found",
            ) from exc
        if "access" in low or "permission" in low or "denied" in low:
            raise ProductFetchError(
                f"Daraz rejected access to item {item_id} for this store",
                code="access_denied",
            ) from exc
        raise ProductFetchError(
            f"Daraz error fetching item {item_id}: {exc.code}:{msg}"[:240],
            code="daraz_error",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProductFetchError(
            f"Failed to fetch item {item_id}: {type(exc).__name__}:{exc}"[:240],
            code="fetch_failed",
        ) from exc
    api_ms = (time.perf_counter() - t_api) * 1000

    data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
    if not isinstance(data, dict) or not (data.get("item_id") or data.get("itemId")):
        # Some responses nest under data without item_id when empty
        raise ProductFetchError(
            f"Item {item_id} is unavailable or not owned by the selected store",
            code="item_unavailable",
        )

    returned_id = str(data.get("item_id") or data.get("itemId") or "")
    if returned_id and returned_id != item_id:
        raise ProductFetchError(
            f"Daraz returned item {returned_id} which does not match requested {item_id}",
            code="item_mismatch",
        )

    t_norm = time.perf_counter()
    # Normalization is inside upsert helpers; measure payload build lightly
    _ = product_payload_from_daraz(workspace_id, store_uuid, data)
    norm_ms = (time.perf_counter() - t_norm) * 1000

    product, variants, db_ms = _upsert_detail_product(workspace_id, store_uuid, data)

    return {
        "product": product,
        "variants": variants,
        "source_store": {
            "id": store_uuid,
            "store_id": store.get("store_id"),
            "display_name": store.get("display_name")
            or store.get("store_name")
            or store.get("store_id"),
        },
        "timings_ms": {
            "daraz_item_get": round(api_ms, 1),
            "normalization": round(norm_ms, 1),
            "db_upsert": round(db_ms, 1),
            "total": round(api_ms + norm_ms + db_ms, 1),
        },
        "api_calls": {
            "product_item_get": 1,
            "products_get": 0,
            "other_product_detail": 0,
        },
    }


def ensure_product_detail(
    workspace_id: str,
    product_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Lazy-hydrate a Product Hub row via /product/item/get when incomplete/stale."""
    repo = get_repo()
    product = repo.get_daraz_product(workspace_id, product_id)
    if not product:
        raise ProductFetchError("Product not found in this workspace", code="not_found")

    if not force and is_detail_fresh(product):
        variants = repo.list_daraz_product_variants(workspace_id, product_id)
        return {
            "product": product,
            "variants": variants,
            "hydrated": False,
            "timings_ms": {
                "daraz_item_get": 0,
                "normalization": 0,
                "db_upsert": 0,
                "total": 0,
            },
            "api_calls": {
                "product_item_get": 0,
                "products_get": 0,
                "other_product_detail": 0,
            },
        }

    store = repo.get_store_by_uuid(workspace_id, str(product["store_id"]))
    if not store:
        raise ProductFetchError("Source store not found for product", code="store_not_found")

    fetched = fetch_connected_product(
        workspace_id,
        source_store_id=str(store.get("store_id") or store["id"]),
        daraz_item_id=str(product["daraz_item_id"]),
    )
    fetched["hydrated"] = True
    return fetched


def clone_draft_from_connected(
    workspace_id: str,
    *,
    source_store_id: str,
    daraz_item_id: str,
    destination_store_id: str,
) -> dict[str, Any]:
    """Direct Item ID fetch → upsert → ProductCloneDraft (no catalog sync)."""
    from src.product_clone import build_connected_clone_draft

    t0 = time.perf_counter()
    fetched = fetch_connected_product(
        workspace_id,
        source_store_id=source_store_id,
        daraz_item_id=daraz_item_id,
    )
    draft_result = build_connected_clone_draft(
        workspace_id,
        source_product_id=str(fetched["product"]["id"]),
        destination_store_id=destination_store_id,
    )
    draft_ms = (time.perf_counter() - t0) * 1000 - float(
        fetched["timings_ms"].get("total") or 0
    )
    timings = dict(fetched["timings_ms"])
    timings["clone_draft"] = round(max(draft_ms, 0), 1)
    timings["total"] = round(
        float(fetched["timings_ms"].get("total") or 0) + float(timings["clone_draft"]),
        1,
    )
    return {
        **draft_result,
        "fetched_product": {
            "id": fetched["product"]["id"],
            "daraz_item_id": fetched["product"].get("daraz_item_id"),
            "title": fetched["product"].get("title_en") or fetched["product"].get("title"),
            "detail_complete": fetched["product"].get("detail_complete"),
            "variant_count": len(fetched.get("variants") or []),
        },
        "source_store": fetched["source_store"],
        "timings_ms": timings,
        "api_calls": fetched["api_calls"],
    }
