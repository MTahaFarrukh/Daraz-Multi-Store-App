"""Product create attempt reconciliation and safe retry.

CreateProduct may succeed remotely while the local response is lost.
Never issue a second CreateProduct until Seller SKUs are proven absent.
"""

from __future__ import annotations

import logging
from typing import Any

from src.audit_log import audit_event
from src.daraz_api import DarazApiError, DarazClient
from src.db import get_repo
from src.ops import client_for_store
from src.token_refresh import refresh_one_store

logger = logging.getLogger(__name__)

BLOCK_REMOTE_CREATE_STATES = frozenset(
    {"CREATING", "CREATED_UNVERIFIED", "NEEDS_RECONCILIATION"}
)
RETRY_ALLOWED_STATES = frozenset({"FAILED_SAFE_TO_RETRY"})
VERIFIED_STATES = frozenset({"VERIFIED"})


def normalize_generated_seller_skus(raw: Any) -> list[dict[str, Any]]:
    """Normalize attempt SKU list to [{seller_sku, source_daraz_sku_id?}]."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append({"seller_sku": item.strip(), "source_daraz_sku_id": None})
        elif isinstance(item, dict):
            sku = str(item.get("seller_sku") or item.get("SellerSku") or "").strip()
            if not sku:
                continue
            src = item.get("source_daraz_sku_id") or item.get("daraz_sku_id")
            out.append(
                {
                    "seller_sku": sku,
                    "source_daraz_sku_id": str(src) if src not in (None, "") else None,
                }
            )
    return out


def seller_sku_values(raw: Any) -> list[str]:
    return [r["seller_sku"] for r in normalize_generated_seller_skus(raw)]


def build_generated_seller_skus(draft: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for v in draft.get("variants") or []:
        if not isinstance(v, dict):
            continue
        sku = str(v.get("seller_sku") or "").strip()
        if not sku:
            continue
        src = (
            v.get("source_daraz_sku_id")
            or v.get("daraz_sku_id")
            or v.get("source_variant_id")
        )
        rows.append(
            {
                "seller_sku": sku,
                "source_daraz_sku_id": str(src) if src not in (None, "") else None,
            }
        )
    return rows


def apply_attempt_seller_skus(
    draft: dict[str, Any],
    generated: Any,
) -> dict[str, Any]:
    """Inject persisted Seller SKUs into draft variants; never regenerate.

    Prefer match by source_daraz_sku_id; fall back to stable list order.
    """
    rows = normalize_generated_seller_skus(generated)
    variants = [v for v in (draft.get("variants") or []) if isinstance(v, dict)]
    if not rows or not variants:
        return {"applied": 0, "mode": "noop"}

    by_src: dict[str, str] = {}
    for r in rows:
        if r.get("source_daraz_sku_id"):
            by_src[str(r["source_daraz_sku_id"])] = r["seller_sku"]

    applied = 0
    if by_src and any(
        (v.get("source_daraz_sku_id") or v.get("daraz_sku_id")) for v in variants
    ):
        for v in variants:
            key = str(v.get("source_daraz_sku_id") or v.get("daraz_sku_id") or "")
            if key and key in by_src:
                v["seller_sku"] = by_src[key]
                applied += 1
        return {"applied": applied, "mode": "by_source_sku_id"}

    for idx, v in enumerate(variants):
        if idx >= len(rows):
            break
        v["seller_sku"] = rows[idx]["seller_sku"]
        applied += 1
    return {"applied": applied, "mode": "by_order"}


def build_source_identity(draft: dict[str, Any]) -> str:
    st = str(draft.get("source_type") or "")
    if st in {"connected_store", "connected_via_public_url"}:
        store = draft.get("source_store_id") or ""
        item = (
            draft.get("source_daraz_item_id")
            or draft.get("source_item_id")
            or draft.get("source_product_id")
            or ""
        )
        if store and item:
            return f"connected:{store}:{item}"
    url = draft.get("source_url")
    if url:
        return str(url)
    return str(
        draft.get("source_item_id")
        or draft.get("source_daraz_item_id")
        or st
        or ""
    )


def _item_data(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return data if isinstance(data, dict) else None


def _seller_skus_from_product(product: dict[str, Any]) -> list[str]:
    skus = product.get("skus") or []
    out: list[str] = []
    if not isinstance(skus, list):
        return out
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        val = sku.get("SellerSku") or sku.get("seller_sku")
        if val not in (None, ""):
            out.append(str(val).strip())
    return out


def _sku_mapping_from_product(
    product: dict[str, Any],
    generated: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map source_daraz_sku_id → destination seller/daraz sku ids."""
    want = {r["seller_sku"]: r for r in generated}
    mapping: dict[str, Any] = {}
    skus = product.get("skus") or []
    if not isinstance(skus, list):
        return mapping
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        seller = str(sku.get("SellerSku") or sku.get("seller_sku") or "").strip()
        if seller not in want:
            continue
        src = want[seller].get("source_daraz_sku_id") or seller
        dest_id = sku.get("SkuId") or sku.get("sku_id")
        mapping[str(src)] = {
            "destination_seller_sku": seller,
            "destination_daraz_sku_id": str(dest_id) if dest_id not in (None, "") else None,
            "source_daraz_sku_id": want[seller].get("source_daraz_sku_id"),
        }
    return mapping


def _products_from_catalog(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    products = data.get("products") or data.get("product") or []
    if isinstance(products, dict):
        products = [products]
    return [p for p in products if isinstance(p, dict)] if isinstance(products, list) else []


def _match_product_by_skus(
    products: list[dict[str, Any]],
    expected_skus: list[str],
) -> dict[str, Any] | None:
    expected = {s for s in expected_skus if s}
    if not expected:
        return None
    for product in products:
        found = set(_seller_skus_from_product(product))
        if expected.issubset(found):
            return product
    return None


def _client_for_destination(workspace_id: str, dest: dict[str, Any]) -> DarazClient:
    """Refresh store token if possible, then build client."""
    repo = get_repo()
    store = dest
    try:
        store = refresh_one_store(
            dest,
            upsert_fn=lambda record: repo.upsert_store(workspace_id, record),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "Token refresh skipped for reconcile store %s: %s",
            dest.get("store_id"),
            exc,
        )
        store = repo.get_store_by_uuid(workspace_id, str(dest.get("id"))) or dest
    return client_for_store(store)


def _resolve_destination_store(
    workspace_id: str, destination_store_id: str
) -> dict[str, Any] | None:
    repo = get_repo()
    return repo.get_store_by_uuid(workspace_id, destination_store_id) or repo.get_store(
        workspace_id, destination_store_id
    )


def reconcile_product_create_attempt(
    workspace_id: str,
    attempt_id: str,
    *,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Reconcile an uncertain create attempt against the destination store."""
    repo = get_repo()
    attempt = repo.get_product_attempt(workspace_id, attempt_id)
    if not attempt:
        return {"status": "NOT_FOUND", "reason": "attempt_not_found"}

    audit_event(
        repo,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action="product.reconcile.requested",
        entity_type="product_create_attempt",
        entity_id=str(attempt_id),
        metadata={
            "attempt_id": str(attempt_id),
            "destination_store_id": attempt.get("destination_store_id"),
            "source_identity": attempt.get("source_identity"),
            "state": attempt.get("state"),
        },
    )

    if attempt.get("state") in VERIFIED_STATES:
        return {
            "status": "VERIFIED",
            "attempt": attempt,
            "reason": "already_verified",
        }

    dest = _resolve_destination_store(
        workspace_id, str(attempt.get("destination_store_id") or "")
    )
    if not dest:
        updated = repo.update_product_attempt(
            workspace_id,
            attempt_id,
            state="NEEDS_RECONCILIATION",
            last_error="destination_store_not_found",
        )
        return {
            "status": "NEEDS_RECONCILIATION",
            "attempt": updated or attempt,
            "reason": "destination_store_not_found",
        }

    generated = normalize_generated_seller_skus(attempt.get("generated_seller_skus"))
    expected_skus = [g["seller_sku"] for g in generated]
    if not expected_skus:
        updated = repo.update_product_attempt(
            workspace_id,
            attempt_id,
            state="NEEDS_RECONCILIATION",
            last_error="missing_generated_seller_skus",
        )
        return {
            "status": "NEEDS_RECONCILIATION",
            "attempt": updated or attempt,
            "reason": "missing_generated_seller_skus",
        }

    try:
        client = _client_for_destination(workspace_id, dest)
    except Exception as exc:  # noqa: BLE001
        updated = repo.update_product_attempt(
            workspace_id,
            attempt_id,
            state="NEEDS_RECONCILIATION",
            last_error=f"token:{exc}",
        )
        return {
            "status": "NEEDS_RECONCILIATION",
            "attempt": updated or attempt,
            "reason": "token_unavailable",
        }

    matched: dict[str, Any] | None = None
    conclusive_absent = False
    lookup_uncertain = False
    item_id = attempt.get("destination_item_id")

    # A. Known destination item id
    if item_id:
        try:
            resp = client.get_product_item(item_id)
            data = _item_data(resp)
            if data and _match_product_by_skus([data], expected_skus):
                matched = data
            elif data:
                # Item exists but SKUs do not match this attempt — do not claim verified
                lookup_uncertain = True
            else:
                lookup_uncertain = True
        except DarazApiError as exc:
            low = str(exc).lower()
            code = str(exc.code or "")
            if code in {"404", "NotFound"} or "not found" in low or "does not exist" in low:
                # Fall through to SKU catalog search
                item_id = None
            else:
                lookup_uncertain = True
        except Exception:  # noqa: BLE001
            lookup_uncertain = True

    # B. Search by exact persisted Seller SKUs
    if matched is None and not lookup_uncertain:
        try:
            sku_list = ",".join(expected_skus)
            catalog = client.get_products(
                filter="all",
                offset=0,
                limit=max(20, len(expected_skus) * 2),
                options="1",
                sku_seller_list=sku_list,
            )
            products = _products_from_catalog(catalog)
            matched = _match_product_by_skus(products, expected_skus)
            if matched is None:
                # Empty successful catalog page for exact sku_seller_list → absent
                code = str(catalog.get("code") or "0")
                if code in {"0", "Success", "success"} and not products:
                    conclusive_absent = True
                elif products:
                    # Products returned but none match all SKUs — still uncertain
                    # (partial/filter quirks). Try per-SKU detail if single product
                    # shares any SKU.
                    for p in products:
                        found = set(_seller_skus_from_product(p))
                        if found & set(expected_skus):
                            # Partial overlap without full set — fetch item for certainty
                            iid = p.get("item_id")
                            if iid is not None:
                                try:
                                    detail = client.get_product_item(iid)
                                    data = _item_data(detail)
                                    if data and _match_product_by_skus(
                                        [data], expected_skus
                                    ):
                                        matched = data
                                        break
                                except DarazApiError:
                                    lookup_uncertain = True
                                    break
                    if matched is None and not lookup_uncertain and not conclusive_absent:
                        # Successful API, no full match after checking overlaps
                        if not any(
                            set(_seller_skus_from_product(p)) & set(expected_skus)
                            for p in products
                        ):
                            conclusive_absent = True
                        else:
                            lookup_uncertain = True
        except DarazApiError:
            lookup_uncertain = True
        except Exception:  # noqa: BLE001
            lookup_uncertain = True

    if matched is not None:
        dest_item = str(
            matched.get("item_id") or attempt.get("destination_item_id") or ""
        )
        mapping = _sku_mapping_from_product(matched, generated)
        updated = repo.update_product_attempt(
            workspace_id,
            attempt_id,
            state="VERIFIED",
            verification_state="VERIFIED",
            destination_item_id=dest_item or None,
            destination_sku_mapping=mapping,
            last_error=None,
        )
        audit_event(
            repo,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="product.reconcile.verified",
            entity_type="product_create_attempt",
            entity_id=str(attempt_id),
            metadata={
                "attempt_id": str(attempt_id),
                "destination_store_id": attempt.get("destination_store_id"),
                "destination_item_id": dest_item,
                "state": "VERIFIED",
            },
        )
        return {
            "status": "VERIFIED",
            "attempt": updated or attempt,
            "destination_item_id": dest_item,
            "destination_sku_mapping": mapping,
            "reason": "matched_seller_skus",
        }

    if conclusive_absent and not lookup_uncertain:
        updated = repo.update_product_attempt(
            workspace_id,
            attempt_id,
            state="FAILED_SAFE_TO_RETRY",
            verification_state="UNVERIFIED",
            last_error="seller_skus_not_found_on_destination",
        )
        audit_event(
            repo,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="product.reconcile.unresolved",
            entity_type="product_create_attempt",
            entity_id=str(attempt_id),
            metadata={
                "attempt_id": str(attempt_id),
                "destination_store_id": attempt.get("destination_store_id"),
                "state": "FAILED_SAFE_TO_RETRY",
                "reason": "seller_skus_not_found",
            },
        )
        return {
            "status": "FAILED_SAFE_TO_RETRY",
            "attempt": updated or attempt,
            "reason": "seller_skus_not_found_on_destination",
        }

    updated = repo.update_product_attempt(
        workspace_id,
        attempt_id,
        state="NEEDS_RECONCILIATION",
        verification_state="UNVERIFIED",
        last_error="reconcile_inconclusive",
    )
    audit_event(
        repo,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action="product.reconcile.unresolved",
        entity_type="product_create_attempt",
        entity_id=str(attempt_id),
        metadata={
            "attempt_id": str(attempt_id),
            "destination_store_id": attempt.get("destination_store_id"),
            "state": "NEEDS_RECONCILIATION",
            "reason": "inconclusive",
        },
    )
    return {
        "status": "NEEDS_RECONCILIATION",
        "attempt": updated or attempt,
        "reason": "inconclusive_lookup",
    }


def retry_product_create_attempt(
    workspace_id: str,
    attempt_id: str,
    *,
    actor_user_id: str | None = None,
    execute: bool = True,
) -> dict[str, Any]:
    """Safe retry: only FAILED_SAFE_TO_RETRY; reuse exact Seller SKUs."""
    from src.product_add import (
        add_product_from_connected,
        add_product_from_public_url,
    )

    repo = get_repo()
    attempt = repo.get_product_attempt(workspace_id, attempt_id)
    if not attempt:
        return {"status": "NOT_FOUND", "reason": "attempt_not_found"}

    audit_event(
        repo,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action="product.retry.requested",
        entity_type="product_create_attempt",
        entity_id=str(attempt_id),
        metadata={
            "attempt_id": str(attempt_id),
            "destination_store_id": attempt.get("destination_store_id"),
            "state": attempt.get("state"),
        },
    )

    state = str(attempt.get("state") or "")
    if state in VERIFIED_STATES:
        return {
            "status": "VERIFIED",
            "attempt": attempt,
            "reason": "already_verified",
            "create_product_called": False,
        }
    if state in BLOCK_REMOTE_CREATE_STATES:
        return {
            "status": "BLOCKED",
            "attempt": attempt,
            "reason": "requires_reconciliation_before_retry",
            "create_product_called": False,
        }
    if state not in RETRY_ALLOWED_STATES:
        return {
            "status": "BLOCKED",
            "attempt": attempt,
            "reason": f"retry_not_allowed_from_{state}",
            "create_product_called": False,
        }

    dest = _resolve_destination_store(
        workspace_id, str(attempt.get("destination_store_id") or "")
    )
    if not dest:
        return {
            "status": "FAILED",
            "reason": "destination_store_not_found",
            "create_product_called": False,
        }

    dest_ref = str(dest.get("store_id") or dest.get("id"))
    source_identity = str(attempt.get("source_identity") or "")
    source_type = str(attempt.get("source_type") or "")
    generated = attempt.get("generated_seller_skus")

    # Mark CREATING and bump retry_count before remote write path
    repo.update_product_attempt(
        workspace_id,
        attempt_id,
        state="CREATING",
        retry_count=int(attempt.get("retry_count") or 0) + 1,
        last_error=None,
    )

    try:
        if source_identity.startswith("connected:"):
            # connected:{store_id}:{item_id}
            parts = source_identity.split(":", 2)
            src_store = parts[1] if len(parts) > 1 else ""
            item_id = parts[2] if len(parts) > 2 else ""
            result = add_product_from_connected(
                workspace_id,
                src_store,
                item_id,
                [dest_ref],
                execute=execute,
                forced_attempt_id=str(attempt_id),
                forced_seller_skus=generated,
            )
        elif source_identity.startswith("http"):
            result = add_product_from_public_url(
                workspace_id,
                source_identity,
                [dest_ref],
                execute=execute,
                forced_attempt_id=str(attempt_id),
                forced_seller_skus=generated,
            )
        elif source_type in {"public_daraz_url", "connected_via_public_url"} and source_identity.isdigit():
            url = f"https://www.daraz.pk/products/i{source_identity}.html"
            result = add_product_from_public_url(
                workspace_id,
                url,
                [dest_ref],
                execute=execute,
                forced_attempt_id=str(attempt_id),
                forced_seller_skus=generated,
            )
        else:
            repo.update_product_attempt(
                workspace_id,
                attempt_id,
                state="FAILED_SAFE_TO_RETRY",
                last_error="retry_source_unavailable",
            )
            return {
                "status": "FAILED",
                "reason": "retry_source_unavailable",
                "create_product_called": False,
                "attempt": repo.get_product_attempt(workspace_id, attempt_id),
            }
    except Exception as exc:  # noqa: BLE001
        repo.update_product_attempt(
            workspace_id,
            attempt_id,
            state="NEEDS_RECONCILIATION",
            last_error=f"retry_exception:{type(exc).__name__}",
        )
        audit_event(
            repo,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="product.retry.failed",
            entity_type="product_create_attempt",
            entity_id=str(attempt_id),
            metadata={
                "attempt_id": str(attempt_id),
                "error": type(exc).__name__,
                "state": "NEEDS_RECONCILIATION",
            },
        )
        return {
            "status": "NEEDS_RECONCILIATION",
            "reason": "retry_exception",
            "create_product_called": True,
            "attempt": repo.get_product_attempt(workspace_id, attempt_id),
        }

    refreshed = repo.get_product_attempt(workspace_id, attempt_id)
    dest_results = (result or {}).get("destinations") or []
    dest_one = dest_results[0] if dest_results else result
    final_state = (refreshed or {}).get("state")
    action = (
        "product.retry.verified"
        if final_state == "VERIFIED"
        else "product.retry.failed"
    )
    audit_event(
        repo,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type="product_create_attempt",
        entity_id=str(attempt_id),
        metadata={
            "attempt_id": str(attempt_id),
            "destination_store_id": attempt.get("destination_store_id"),
            "state": final_state,
            "destination_status": (dest_one or {}).get("status")
            if isinstance(dest_one, dict)
            else None,
        },
    )
    return {
        "status": final_state or (dest_one or {}).get("status"),
        "attempt": refreshed,
        "result": result,
        "create_product_called": True,
        "reason": "retry_executed",
    }
