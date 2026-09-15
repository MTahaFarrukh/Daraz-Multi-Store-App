"""Repository interface and factory for workspace tenancy data."""

from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol

from src.config import get_env
from src.crypto_tokens import decrypt_secret, encrypt_secret


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def store_row_to_record(row: dict[str, Any], *, include_tokens: bool = True) -> dict[str, Any]:
    """Normalize a DB/memory store row into the dict shape used by ops/token_store."""
    record: dict[str, Any] = {
        "id": str(row["id"]) if row.get("id") else None,
        "store_id": row.get("store_id", ""),
        "display_name": row.get("display_name") or row.get("store_name") or "",
        "store_name": row.get("store_name") or row.get("display_name") or "",
        "account": row.get("account", ""),
        "seller_id": row.get("seller_id", ""),
        "user_id": row.get("daraz_user_id") or row.get("user_id"),
        "country": row.get("country", ""),
        "account_platform": row.get("account_platform", ""),
        "country_user_info": row.get("country_user_info") or [],
        "expires_in": row.get("expires_in"),
        "refresh_expires_in": row.get("refresh_expires_in"),
        "access_token_expires_at": (
            row["access_token_expires_at"].isoformat()
            if isinstance(row.get("access_token_expires_at"), datetime)
            else row.get("access_token_expires_at")
        ),
        "refresh_token_expires_at": (
            row["refresh_token_expires_at"].isoformat()
            if isinstance(row.get("refresh_token_expires_at"), datetime)
            else row.get("refresh_token_expires_at")
        ),
        "authorized_at": (
            row["authorized_at"].isoformat()
            if isinstance(row.get("authorized_at"), datetime)
            else row.get("authorized_at")
        ),
        "updated_at": (
            row["updated_at"].isoformat()
            if isinstance(row.get("updated_at"), datetime)
            else row.get("updated_at")
        ),
        "request_id": row.get("request_id"),
        "workspace_id": str(row.get("workspace_id", "")),
    }
    if include_tokens:
        access_enc = row.get("access_token_enc") or row.get("access_token", "")
        refresh_enc = row.get("refresh_token_enc") or row.get("refresh_token", "")
        # Rows may already hold plaintext in memory test fixtures.
        if row.get("access_token") and not row.get("access_token_enc"):
            record["access_token"] = row["access_token"]
            record["refresh_token"] = row.get("refresh_token", "")
        else:
            record["access_token"] = decrypt_secret(str(access_enc))
            record["refresh_token"] = decrypt_secret(str(refresh_enc))
    return record


class TenancyRepo(Protocol):
    def create_workspace_with_owner(self, user_id: str, name: str) -> dict[str, Any]: ...

    def list_memberships(self, user_id: str) -> list[dict[str, Any]]: ...

    def get_membership(self, workspace_id: str, user_id: str) -> dict[str, Any] | None: ...

    def require_membership(self, workspace_id: str, user_id: str) -> dict[str, Any]: ...

    def list_stores(self, workspace_id: str) -> list[dict[str, Any]]: ...

    def get_store(self, workspace_id: str, store_id: str) -> dict[str, Any] | None: ...

    def upsert_store(self, workspace_id: str, record: dict[str, Any]) -> dict[str, Any]: ...

    def update_store_display_name(
        self, workspace_id: str, store_id: str, display_name: str
    ) -> dict[str, Any]: ...

    def list_groups(self, workspace_id: str) -> list[dict[str, Any]]: ...

    def create_group(
        self, workspace_id: str, name: str, store_ids: list[str]
    ) -> dict[str, Any]: ...

    def update_group(
        self,
        workspace_id: str,
        group_id: str,
        *,
        name: str | None = None,
        store_ids: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def delete_group(self, workspace_id: str, group_id: str) -> None: ...

    def save_print_job(self, job: dict[str, Any]) -> None: ...

    def get_print_job(self, job_id: str) -> dict[str, Any] | None: ...

    def list_print_jobs(
        self, workspace_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    def get_store_by_uuid(
        self, workspace_id: str, store_uuid: str
    ) -> dict[str, Any] | None: ...

    def upsert_store_performance(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_store_performance(
        self, workspace_id: str, store_uuid: str, year: int, month: int
    ) -> dict[str, Any] | None: ...

    def list_store_performance(
        self, workspace_id: str, year: int, month: int
    ) -> list[dict[str, Any]]: ...

    def list_performance_months(self, workspace_id: str) -> list[dict[str, int]]: ...

    def upsert_daraz_order(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def upsert_daraz_order_item(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get_order_by_id(
        self, workspace_id: str, order_uuid: str
    ) -> dict[str, Any] | None: ...

    def list_orders(
        self, workspace_id: str, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    def count_orders_by_status_group(
        self, workspace_id: str, store_uuids: list[str] | None = None
    ) -> dict[str, int]: ...

    def list_order_items(
        self, workspace_id: str, order_uuid: str
    ) -> list[dict[str, Any]]: ...

    def list_label_prints_for_orders(
        self, workspace_id: str, order_uuids: list[str]
    ) -> dict[str, list[dict[str, Any]]]: ...

    def has_label_print(
        self, workspace_id: str, store_uuid: str, daraz_order_id: str
    ) -> bool: ...

    def count_label_prints(
        self, workspace_id: str, store_uuid: str, daraz_order_id: str
    ) -> int: ...

    def get_print_summary_for_order(
        self, workspace_id: str, order_uuid: str
    ) -> dict[str, Any]: ...

    def insert_label_print(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class MemoryTenancyRepo:
    """In-memory repo for tests and local AUTH_TEST_MODE without Postgres."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.members: list[dict[str, Any]] = []
        self.stores: dict[str, dict[str, Any]] = {}  # key: workspace_id|store_id
        self.groups: dict[str, dict[str, Any]] = {}
        self.group_members: dict[str, list[str]] = {}
        self.print_jobs: dict[str, dict[str, Any]] = {}
        self.performance: dict[str, dict[str, Any]] = {}  # store_uuid|y|m
        self.orders: dict[str, dict[str, Any]] = {}  # order uuid → row
        self.order_items: dict[str, dict[str, Any]] = {}  # item uuid → row
        self.label_prints: dict[str, dict[str, Any]] = {}  # print uuid → row

    def create_workspace_with_owner(self, user_id: str, name: str) -> dict[str, Any]:
        with self._lock:
            existing = [m for m in self.members if m["user_id"] == user_id]
            if existing:
                ws = self.workspaces[existing[0]["workspace_id"]]
                return {
                    "workspace": deepcopy(ws),
                    "membership": deepcopy(existing[0]),
                    "created": False,
                }
            wid = _uuid()
            ws = {"id": wid, "name": name, "created_at": _now().isoformat()}
            member = {
                "workspace_id": wid,
                "user_id": user_id,
                "role": "owner",
                "created_at": _now().isoformat(),
            }
            self.workspaces[wid] = ws
            self.members.append(member)
            return {
                "workspace": deepcopy(ws),
                "membership": deepcopy(member),
                "created": True,
            }

    def list_memberships(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for m in self.members:
                if m["user_id"] != user_id:
                    continue
                ws = self.workspaces.get(m["workspace_id"])
                if not ws:
                    continue
                out.append(
                    {
                        **deepcopy(m),
                        "workspace_name": ws["name"],
                    }
                )
            return out

    def get_membership(self, workspace_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            for m in self.members:
                if m["workspace_id"] == workspace_id and m["user_id"] == user_id:
                    return deepcopy(m)
            return None

    def require_membership(self, workspace_id: str, user_id: str) -> dict[str, Any]:
        membership = self.get_membership(workspace_id, user_id)
        if not membership:
            raise PermissionError("Not a member of this workspace")
        return membership

    def _store_key(self, workspace_id: str, store_id: str) -> str:
        return f"{workspace_id}|{store_id.lower()}"

    def list_stores(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                store_row_to_record(s)
                for k, s in self.stores.items()
                if s["workspace_id"] == workspace_id
            ]
            return rows

    def get_store(self, workspace_id: str, store_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.stores.get(self._store_key(workspace_id, store_id))
            if not row:
                # also match by account
                needle = store_id.strip().lower()
                for s in self.stores.values():
                    if s["workspace_id"] != workspace_id:
                        continue
                    if str(s.get("store_id", "")).lower() == needle:
                        return store_row_to_record(s)
                    if str(s.get("account", "")).lower() == needle:
                        return store_row_to_record(s)
                return None
            return store_row_to_record(row)

    def upsert_store(self, workspace_id: str, record: dict[str, Any]) -> dict[str, Any]:
        from src.store_display import looks_like_email
        from src.token_store import _ensure_store_identity

        store = _ensure_store_identity(dict(record))
        with self._lock:
            match_key = None
            account = str(store.get("account", "")).lower()
            seller_id = str(store.get("seller_id", "")).lower()
            store_id = str(store.get("store_id", "")).lower()
            for key, existing in self.stores.items():
                if existing["workspace_id"] != workspace_id:
                    continue
                if account and str(existing.get("account", "")).lower() == account:
                    match_key = key
                    break
                if seller_id and str(existing.get("seller_id", "")).lower() == seller_id:
                    match_key = key
                    break
                if store_id and str(existing.get("store_id", "")).lower() == store_id:
                    match_key = key
                    break

            if match_key:
                prior = self.stores[match_key]
                prior_display = str(
                    prior.get("display_name") or prior.get("store_name") or ""
                ).strip()
                if prior_display and not looks_like_email(prior_display):
                    store["display_name"] = prior_display
                prior_shop = str(prior.get("store_name") or "").strip()
                if prior_shop and not looks_like_email(prior_shop):
                    store["store_name"] = prior_shop
                store["store_id"] = prior.get("store_id") or store["store_id"]

            row = {
                "id": (self.stores[match_key]["id"] if match_key else _uuid()),
                "workspace_id": workspace_id,
                "store_id": store["store_id"],
                "display_name": store.get("display_name") or store.get("store_name") or "",
                "store_name": store.get("store_name") or store.get("display_name") or "",
                "account": store.get("account", ""),
                "seller_id": store.get("seller_id", ""),
                "daraz_user_id": str(store.get("user_id") or "") or None,
                "country": store.get("country", ""),
                "account_platform": store.get("account_platform", ""),
                "country_user_info": store.get("country_user_info") or [],
                "access_token_enc": encrypt_secret(str(store.get("access_token", ""))),
                "refresh_token_enc": encrypt_secret(str(store.get("refresh_token", ""))),
                "expires_in": store.get("expires_in"),
                "refresh_expires_in": store.get("refresh_expires_in"),
                "access_token_expires_at": _parse_ts(store.get("access_token_expires_at")),
                "refresh_token_expires_at": _parse_ts(store.get("refresh_token_expires_at")),
                "authorized_at": _parse_ts(store.get("authorized_at")),
                "request_id": store.get("request_id"),
                "updated_at": _now(),
            }
            self.stores[self._store_key(workspace_id, row["store_id"])] = row
            if match_key and match_key != self._store_key(workspace_id, row["store_id"]):
                del self.stores[match_key]
            return store_row_to_record(row)

    def update_store_display_name(
        self, workspace_id: str, store_id: str, display_name: str
    ) -> dict[str, Any]:
        name = display_name.strip()
        if not name:
            raise ValueError("Store name cannot be empty")
        with self._lock:
            key = self._store_key(workspace_id, store_id)
            row = self.stores.get(key)
            if not row:
                raise ValueError(f"Unknown store: {store_id}")
            row = dict(row)
            row["display_name"] = name
            row["updated_at"] = _now()
            self.stores[key] = row
            return store_row_to_record(row)

    def list_groups(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for g in self.groups.values():
                if g["workspace_id"] != workspace_id:
                    continue
                out.append(
                    {
                        "id": g["id"],
                        "name": g["name"],
                        "store_ids": list(self.group_members.get(g["id"], [])),
                        "created_at": g.get("created_at"),
                        "updated_at": g.get("updated_at"),
                    }
                )
            out.sort(key=lambda x: x["name"].lower())
            return out

    def create_group(
        self, workspace_id: str, name: str, store_ids: list[str]
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Group name cannot be empty")
        with self._lock:
            for g in self.groups.values():
                if g["workspace_id"] == workspace_id and g["name"].lower() == name.lower():
                    # update existing by name (save profile semantics)
                    g["name"] = name
                    g["updated_at"] = _now().isoformat()
                    self.group_members[g["id"]] = list(dict.fromkeys(store_ids))
                    return {
                        "id": g["id"],
                        "name": g["name"],
                        "store_ids": list(self.group_members[g["id"]]),
                    }
            gid = _uuid()
            self.groups[gid] = {
                "id": gid,
                "workspace_id": workspace_id,
                "name": name,
                "created_at": _now().isoformat(),
                "updated_at": _now().isoformat(),
            }
            self.group_members[gid] = list(dict.fromkeys(store_ids))
            return {
                "id": gid,
                "name": name,
                "store_ids": list(self.group_members[gid]),
            }

    def update_group(
        self,
        workspace_id: str,
        group_id: str,
        *,
        name: str | None = None,
        store_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            g = self.groups.get(group_id)
            if not g or g["workspace_id"] != workspace_id:
                raise ValueError("Group not found")
            if name is not None:
                cleaned = name.strip()
                if not cleaned:
                    raise ValueError("Group name cannot be empty")
                g["name"] = cleaned
            if store_ids is not None:
                self.group_members[group_id] = list(dict.fromkeys(store_ids))
            g["updated_at"] = _now().isoformat()
            return {
                "id": g["id"],
                "name": g["name"],
                "store_ids": list(self.group_members.get(group_id, [])),
            }

    def delete_group(self, workspace_id: str, group_id: str) -> None:
        with self._lock:
            g = self.groups.get(group_id)
            if not g or g["workspace_id"] != workspace_id:
                raise ValueError("Group not found")
            del self.groups[group_id]
            self.group_members.pop(group_id, None)

    def save_print_job(self, job: dict[str, Any]) -> None:
        with self._lock:
            self.print_jobs[str(job["id"])] = deepcopy(job)

    def get_print_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self.print_jobs.get(str(job_id))
            return deepcopy(job) if job else None

    def list_print_jobs(
        self, workspace_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                deepcopy(j)
                for j in self.print_jobs.values()
                if str(j.get("workspace_id")) == str(workspace_id)
            ]
        rows.sort(key=lambda j: str(j.get("updated_at") or j.get("started_at") or ""), reverse=True)
        return rows[: max(1, min(limit, 100))]

    def get_store_by_uuid(
        self, workspace_id: str, store_uuid: str
    ) -> dict[str, Any] | None:
        with self._lock:
            for s in self.stores.values():
                if s["workspace_id"] == workspace_id and str(s.get("id")) == str(store_uuid):
                    return store_row_to_record(s)
            return None

    def _perf_key(self, store_uuid: str, year: int, month: int) -> str:
        return f"{store_uuid}|{year}|{month}"

    def upsert_store_performance(self, **kwargs: Any) -> dict[str, Any]:
        workspace_id = str(kwargs["workspace_id"])
        store_uuid = str(kwargs["store_uuid"])
        year = int(kwargs["year"])
        month = int(kwargs["month"])
        preserve = bool(kwargs.get("preserve_counts_on_error"))
        key = self._perf_key(store_uuid, year, month)
        with self._lock:
            existing = self.performance.get(key)
            if preserve and existing and kwargs.get("sync_status") == "error":
                row = dict(existing)
                row["sync_status"] = "error"
                row["sync_error"] = kwargs.get("sync_error")
                row["updated_at"] = _now().isoformat()
                self.performance[key] = row
                return deepcopy(row)

            now = _now().isoformat()
            row = {
                "id": (existing or {}).get("id") or _uuid(),
                "workspace_id": workspace_id,
                "store_id": store_uuid,
                "year": year,
                "month": month,
                "orders_count": int(kwargs.get("orders_count") or 0),
                "gross_sales": kwargs.get("gross_sales"),
                "currency": kwargs.get("currency") or "PKR",
                "previous_orders_count": kwargs.get("previous_orders_count"),
                "orders_growth_pct": kwargs.get("orders_growth_pct"),
                "previous_gross_sales": kwargs.get("previous_gross_sales"),
                "gross_sales_growth_pct": kwargs.get("gross_sales_growth_pct"),
                "source": kwargs.get("source") or "orders_api",
                "sync_status": kwargs.get("sync_status") or "ok",
                "sync_error": kwargs.get("sync_error"),
                "orders_synced_at": (
                    now if kwargs.get("orders_synced") else (existing or {}).get("orders_synced_at")
                ),
                "gross_sales_synced_at": (
                    now
                    if kwargs.get("gross_synced")
                    else (existing or {}).get("gross_sales_synced_at")
                ),
                "created_at": (existing or {}).get("created_at") or now,
                "updated_at": now,
            }
            self.performance[key] = row
            return deepcopy(row)

    def get_store_performance(
        self, workspace_id: str, store_uuid: str, year: int, month: int
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self.performance.get(self._perf_key(store_uuid, year, month))
            if not row or str(row.get("workspace_id")) != str(workspace_id):
                return None
            return deepcopy(row)

    def list_store_performance(
        self, workspace_id: str, year: int, month: int
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                deepcopy(r)
                for r in self.performance.values()
                if str(r.get("workspace_id")) == str(workspace_id)
                and int(r.get("year")) == year
                and int(r.get("month")) == month
            ]
        return rows

    def list_performance_months(self, workspace_id: str) -> list[dict[str, int]]:
        with self._lock:
            pairs = {
                (int(r["year"]), int(r["month"]))
                for r in self.performance.values()
                if str(r.get("workspace_id")) == str(workspace_id)
            }
        return [{"year": y, "month": m} for y, m in sorted(pairs, reverse=True)]

    def _order_key_by_store_daraz(self, store_uuid: str, daraz_order_id: str) -> str:
        return f"{store_uuid}|{daraz_order_id}"

    def _item_key_by_store_daraz(self, store_uuid: str, daraz_item_id: str) -> str:
        return f"{store_uuid}|{daraz_item_id}"

    def upsert_daraz_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload["workspace_id"])
        store_uuid = str(payload["store_id"])
        daraz_order_id = str(payload["daraz_order_id"])
        now = _now().isoformat()
        with self._lock:
            existing = None
            for row in self.orders.values():
                if (
                    str(row.get("store_id")) == store_uuid
                    and str(row.get("daraz_order_id")) == daraz_order_id
                ):
                    existing = row
                    break
            row = {
                "id": (existing or {}).get("id") or _uuid(),
                "workspace_id": workspace_id,
                "store_id": store_uuid,
                "daraz_order_id": daraz_order_id,
                "order_number": payload.get("order_number"),
                "status_raw": payload.get("status_raw"),
                "status_group": payload.get("status_group") or "other",
                "statuses": deepcopy(payload.get("statuses")),
                "created_at_daraz": payload.get("created_at_daraz"),
                "updated_at_daraz": payload.get("updated_at_daraz"),
                "price": payload.get("price"),
                "currency": payload.get("currency"),
                "items_count": payload.get("items_count"),
                "customer_first_name": payload.get("customer_first_name"),
                "customer_last_name": payload.get("customer_last_name"),
                "address_shipping": deepcopy(payload.get("address_shipping")),
                "address_billing": deepcopy(payload.get("address_billing")),
                "payment_method": payload.get("payment_method"),
                "shipping_fee": payload.get("shipping_fee"),
                "warehouse_code": payload.get("warehouse_code"),
                "synced_at": now,
                "created_at": (existing or {}).get("created_at") or now,
                "updated_at": now,
            }
            self.orders[str(row["id"])] = row
            return deepcopy(row)

    def upsert_daraz_order_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload["workspace_id"])
        store_uuid = str(payload["store_id"])
        daraz_item_id = str(payload["daraz_order_item_id"])
        now = _now().isoformat()
        with self._lock:
            existing = None
            for row in self.order_items.values():
                if (
                    str(row.get("store_id")) == store_uuid
                    and str(row.get("daraz_order_item_id")) == daraz_item_id
                ):
                    existing = row
                    break
            row = {
                "id": (existing or {}).get("id") or _uuid(),
                "workspace_id": workspace_id,
                "store_id": store_uuid,
                "order_id": str(payload["order_id"]),
                "daraz_order_item_id": daraz_item_id,
                "daraz_order_id": payload.get("daraz_order_id"),
                "status_raw": payload.get("status_raw"),
                "package_id": payload.get("package_id"),
                "name": payload.get("name"),
                "sku": payload.get("sku"),
                "sku_id": payload.get("sku_id"),
                "product_id": payload.get("product_id"),
                "quantity": int(payload.get("quantity") or 1),
                "item_price": payload.get("item_price"),
                "paid_price": payload.get("paid_price"),
                "currency": payload.get("currency"),
                "tracking_code": payload.get("tracking_code"),
                "shipment_provider": payload.get("shipment_provider"),
                "shipping_type": payload.get("shipping_type"),
                "warehouse_code": payload.get("warehouse_code"),
                "synced_at": now,
                "created_at": (existing or {}).get("created_at") or now,
                "updated_at": now,
            }
            self.order_items[str(row["id"])] = row
            return deepcopy(row)

    def get_order_by_id(
        self, workspace_id: str, order_uuid: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self.orders.get(str(order_uuid))
            if not row or str(row.get("workspace_id")) != str(workspace_id):
                return None
            return deepcopy(row)

    def _printed_order_ids_unlocked(self, workspace_id: str) -> set[str]:
        return {
            str(p["order_id"])
            for p in self.label_prints.values()
            if str(p.get("workspace_id")) == str(workspace_id)
        }

    def _order_matches_search_unlocked(self, order: dict[str, Any], needle: str) -> bool:
        n = needle.lower()
        fields = [
            order.get("order_number"),
            order.get("daraz_order_id"),
            order.get("customer_first_name"),
            order.get("customer_last_name"),
        ]
        if any(n in str(f).lower() for f in fields if f):
            return True
        for item in self.order_items.values():
            if str(item.get("order_id")) != str(order.get("id")):
                continue
            if n in str(item.get("sku") or "").lower():
                return True
            if n in str(item.get("name") or "").lower():
                return True
        return False

    def list_orders(
        self, workspace_id: str, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        filters = filters or {}
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_size") or 50), 200))
        store_uuids = filters.get("store_uuids")
        store_slugs = filters.get("store_slugs")
        status_group = filters.get("status_group")
        status_raw = filters.get("status_raw")
        search = (filters.get("search") or "").strip()
        date_from = filters.get("date_from")
        date_to = filters.get("date_to")
        print_state = (filters.get("print_state") or "any").lower()
        sort = (filters.get("sort") or "created_at_daraz_desc").lower()

        slug_to_uuid: dict[str, str] = {}
        with self._lock:
            for s in self.stores.values():
                if str(s.get("workspace_id")) == str(workspace_id):
                    slug_to_uuid[str(s.get("store_id", "")).lower()] = str(s["id"])

            allowed_uuids: set[str] | None = None
            if store_uuids is not None:
                allowed_uuids = {str(u) for u in store_uuids}
            if store_slugs is not None:
                slug_uuids = {
                    slug_to_uuid[s.lower()]
                    for s in store_slugs
                    if s and s.lower() in slug_to_uuid
                }
                allowed_uuids = (
                    slug_uuids
                    if allowed_uuids is None
                    else allowed_uuids.intersection(slug_uuids)
                )

            printed_ids = self._printed_order_ids_unlocked(workspace_id)
            rows = []
            for order in self.orders.values():
                if str(order.get("workspace_id")) != str(workspace_id):
                    continue
                if allowed_uuids is not None and str(order.get("store_id")) not in allowed_uuids:
                    continue
                if status_group and str(order.get("status_group")) != str(status_group):
                    continue
                if status_raw:
                    raw = str(order.get("status_raw") or "").lower()
                    if str(status_raw).lower() not in raw:
                        continue
                if date_from and (order.get("created_at_daraz") or "") < str(date_from):
                    continue
                if date_to and (order.get("created_at_daraz") or "") > str(date_to):
                    continue
                oid = str(order["id"])
                if print_state == "printed" and oid not in printed_ids:
                    continue
                if print_state == "unprinted" and oid in printed_ids:
                    continue
                if search and not self._order_matches_search_unlocked(order, search):
                    continue
                rows.append(deepcopy(order))

            label_prints_snapshot = [
                deepcopy(p)
                for p in self.label_prints.values()
                if str(p.get("workspace_id")) == str(workspace_id)
            ]

        reverse = True
        key_fn = lambda o: str(o.get("created_at_daraz") or "")  # noqa: E731
        if sort in {"created_at_asc", "created_at_daraz_asc"}:
            reverse = False
        elif sort in {"updated_at_desc", "updated_at_daraz_desc"}:
            key_fn = lambda o: str(o.get("updated_at_daraz") or o.get("updated_at") or "")
        elif sort in {"updated_at_asc", "updated_at_daraz_asc"}:
            key_fn = lambda o: str(o.get("updated_at_daraz") or o.get("updated_at") or "")
            reverse = False
        rows.sort(key=key_fn, reverse=reverse)

        total = len(rows)
        start = (page - 1) * page_size
        page_rows = rows[start : start + page_size]
        for order in page_rows:
            oid = str(order["id"])
            prints = [
                p for p in label_prints_snapshot if str(p.get("order_id")) == oid
            ]
            order["print_count"] = len(prints)
            order["has_print"] = len(prints) > 0
            if prints:
                prints_sorted = sorted(
                    prints, key=lambda p: str(p.get("printed_at") or ""), reverse=True
                )
                order["last_printed_at"] = prints_sorted[0].get("printed_at")
            else:
                order["last_printed_at"] = None
        return {
            "items": page_rows,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def count_orders_by_status_group(
        self, workspace_id: str, store_uuids: list[str] | None = None
    ) -> dict[str, int]:
        allowed = {str(u) for u in store_uuids} if store_uuids is not None else None
        counts: dict[str, int] = {}
        with self._lock:
            for order in self.orders.values():
                if str(order.get("workspace_id")) != str(workspace_id):
                    continue
                if allowed is not None and str(order.get("store_id")) not in allowed:
                    continue
                g = str(order.get("status_group") or "other")
                counts[g] = counts.get(g, 0) + 1
        return counts

    def list_order_items(
        self, workspace_id: str, order_uuid: str
    ) -> list[dict[str, Any]]:
        with self._lock:
            order = self.orders.get(str(order_uuid))
            if not order or str(order.get("workspace_id")) != str(workspace_id):
                return []
            return [
                deepcopy(i)
                for i in self.order_items.values()
                if str(i.get("order_id")) == str(order_uuid)
                and str(i.get("workspace_id")) == str(workspace_id)
            ]

    def list_label_prints_for_orders(
        self, workspace_id: str, order_uuids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        wanted = {str(u) for u in order_uuids}
        out: dict[str, list[dict[str, Any]]] = {u: [] for u in wanted}
        with self._lock:
            for p in self.label_prints.values():
                if str(p.get("workspace_id")) != str(workspace_id):
                    continue
                oid = str(p.get("order_id"))
                if oid in wanted:
                    out[oid].append(deepcopy(p))
        for oid in out:
            out[oid].sort(key=lambda x: str(x.get("printed_at") or ""), reverse=True)
        return out

    def has_label_print(
        self, workspace_id: str, store_uuid: str, daraz_order_id: str
    ) -> bool:
        return self.count_label_prints(workspace_id, store_uuid, daraz_order_id) > 0

    def count_label_prints(
        self, workspace_id: str, store_uuid: str, daraz_order_id: str
    ) -> int:
        with self._lock:
            return sum(
                1
                for p in self.label_prints.values()
                if str(p.get("workspace_id")) == str(workspace_id)
                and str(p.get("store_id")) == str(store_uuid)
                and str(p.get("daraz_order_id")) == str(daraz_order_id)
            )

    def get_print_summary_for_order(
        self, workspace_id: str, order_uuid: str
    ) -> dict[str, Any]:
        prints = self.list_label_prints_for_orders(workspace_id, [order_uuid]).get(
            str(order_uuid), []
        )
        last = prints[0] if prints else None
        return {
            "print_count": len(prints),
            "has_print": len(prints) > 0,
            "last_printed_at": last.get("printed_at") if last else None,
            "last_is_reprint": last.get("is_reprint") if last else None,
        }

    def insert_label_print(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = _now().isoformat()
        with self._lock:
            row = {
                "id": payload.get("id") or _uuid(),
                "workspace_id": str(payload["workspace_id"]),
                "store_id": str(payload["store_id"]),
                "order_id": str(payload["order_id"]),
                "daraz_order_id": str(payload["daraz_order_id"]),
                "package_id": payload.get("package_id"),
                "order_item_ids": list(payload.get("order_item_ids") or []),
                "print_job_id": payload.get("print_job_id"),
                "printed_at": payload.get("printed_at") or now,
                "printed_by_user_id": payload.get("printed_by_user_id"),
                "is_reprint": bool(payload.get("is_reprint")),
                "fetch_source": payload.get("fetch_source"),
                "created_at": now,
            }
            self.label_prints[str(row["id"])] = row
            return deepcopy(row)


class PostgresTenancyRepo:
    """Postgres-backed tenancy repository."""

    def __init__(self) -> None:
        from src.db.connection import ensure_saas_schema

        ensure_saas_schema()

    def create_workspace_with_owner(self, user_id: str, name: str) -> dict[str, Any]:
        from src.db.connection import connect

        with connect() as conn:
            existing = conn.execute(
                """
                SELECT m.workspace_id, m.role, m.created_at, w.name, w.created_at AS ws_created
                FROM workspace_members m
                JOIN workspaces w ON w.id = m.workspace_id
                WHERE m.user_id = %s
                ORDER BY m.created_at ASC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if existing:
                return {
                    "workspace": {
                        "id": str(existing[0]),
                        "name": existing[3],
                        "created_at": existing[4].isoformat() if existing[4] else None,
                    },
                    "membership": {
                        "workspace_id": str(existing[0]),
                        "user_id": user_id,
                        "role": existing[1],
                        "created_at": existing[2].isoformat() if existing[2] else None,
                    },
                    "created": False,
                }
            row = conn.execute(
                """
                INSERT INTO workspaces (name) VALUES (%s)
                RETURNING id, name, created_at
                """,
                (name,),
            ).fetchone()
            wid = row[0]
            conn.execute(
                """
                INSERT INTO workspace_members (workspace_id, user_id, role)
                VALUES (%s, %s, 'owner')
                """,
                (wid, user_id),
            )
            conn.commit()
            return {
                "workspace": {
                    "id": str(wid),
                    "name": row[1],
                    "created_at": row[2].isoformat() if row[2] else None,
                },
                "membership": {
                    "workspace_id": str(wid),
                    "user_id": user_id,
                    "role": "owner",
                    "created_at": _now().isoformat(),
                },
                "created": True,
            }

    def list_memberships(self, user_id: str) -> list[dict[str, Any]]:
        from src.db.connection import connect

        with connect() as conn:
            rows = conn.execute(
                """
                SELECT m.workspace_id, m.user_id, m.role, m.created_at, w.name
                FROM workspace_members m
                JOIN workspaces w ON w.id = m.workspace_id
                WHERE m.user_id = %s
                ORDER BY w.created_at ASC
                """,
                (user_id,),
            ).fetchall()
        return [
            {
                "workspace_id": str(r[0]),
                "user_id": str(r[1]),
                "role": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
                "workspace_name": r[4],
            }
            for r in rows
        ]

    def get_membership(self, workspace_id: str, user_id: str) -> dict[str, Any] | None:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                """
                SELECT workspace_id, user_id, role, created_at
                FROM workspace_members
                WHERE workspace_id = %s AND user_id = %s
                """,
                (workspace_id, user_id),
            ).fetchone()
        if not row:
            return None
        return {
            "workspace_id": str(row[0]),
            "user_id": str(row[1]),
            "role": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
        }

    def require_membership(self, workspace_id: str, user_id: str) -> dict[str, Any]:
        membership = self.get_membership(workspace_id, user_id)
        if not membership:
            raise PermissionError("Not a member of this workspace")
        return membership

    def _fetch_store_rows(self, conn, workspace_id: str, store_id: str | None = None):
        if store_id:
            needle = store_id.strip().lower()
            return conn.execute(
                """
                SELECT id, workspace_id, store_id, display_name, store_name, account,
                       seller_id, daraz_user_id, country, account_platform, country_user_info,
                       access_token_enc, refresh_token_enc, expires_in, refresh_expires_in,
                       access_token_expires_at, refresh_token_expires_at, authorized_at, request_id,
                       updated_at
                FROM daraz_stores
                WHERE workspace_id = %s
                  AND (LOWER(store_id) = %s OR LOWER(account) = %s)
                LIMIT 1
                """,
                (workspace_id, needle, needle),
            ).fetchall()
        return conn.execute(
            """
            SELECT id, workspace_id, store_id, display_name, store_name, account,
                   seller_id, daraz_user_id, country, account_platform, country_user_info,
                   access_token_enc, refresh_token_enc, expires_in, refresh_expires_in,
                   access_token_expires_at, refresh_token_expires_at, authorized_at, request_id,
                   updated_at
            FROM daraz_stores
            WHERE workspace_id = %s
            ORDER BY display_name ASC, store_id ASC
            """,
            (workspace_id,),
        ).fetchall()

    def _row_to_dict(self, r) -> dict[str, Any]:
        cui = r[10]
        if isinstance(cui, str):
            try:
                cui = json.loads(cui)
            except json.JSONDecodeError:
                cui = []
        return {
            "id": str(r[0]),
            "workspace_id": str(r[1]),
            "store_id": r[2],
            "display_name": r[3],
            "store_name": r[4],
            "account": r[5],
            "seller_id": r[6],
            "daraz_user_id": r[7],
            "country": r[8],
            "account_platform": r[9],
            "country_user_info": cui or [],
            "access_token_enc": r[11],
            "refresh_token_enc": r[12],
            "expires_in": r[13],
            "refresh_expires_in": r[14],
            "access_token_expires_at": r[15],
            "refresh_token_expires_at": r[16],
            "authorized_at": r[17],
            "request_id": r[18],
            "updated_at": r[19] if len(r) > 19 else None,
        }

    def list_stores(self, workspace_id: str) -> list[dict[str, Any]]:
        from src.db.connection import connect

        with connect() as conn:
            rows = self._fetch_store_rows(conn, workspace_id)
        return [store_row_to_record(self._row_to_dict(r)) for r in rows]

    def get_store(self, workspace_id: str, store_id: str) -> dict[str, Any] | None:
        from src.db.connection import connect

        with connect() as conn:
            rows = self._fetch_store_rows(conn, workspace_id, store_id)
        if not rows:
            return None
        return store_row_to_record(self._row_to_dict(rows[0]))

    def upsert_store(self, workspace_id: str, record: dict[str, Any]) -> dict[str, Any]:
        from src.db.connection import connect
        from src.store_display import looks_like_email
        from src.token_store import _ensure_store_identity

        store = _ensure_store_identity(dict(record))
        with connect() as conn:
            existing = None
            account = str(store.get("account", "")).lower()
            seller_id = str(store.get("seller_id", "")).lower()
            store_id = str(store.get("store_id", "")).lower()
            candidates = conn.execute(
                "SELECT store_id, display_name, store_name, account, seller_id FROM daraz_stores WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchall()
            matched_store_id = None
            for c in candidates:
                if account and str(c[3] or "").lower() == account:
                    matched_store_id = c[0]
                    existing = c
                    break
                if seller_id and str(c[4] or "").lower() == seller_id:
                    matched_store_id = c[0]
                    existing = c
                    break
                if store_id and str(c[0] or "").lower() == store_id:
                    matched_store_id = c[0]
                    existing = c
                    break
            if existing:
                prior_display = str(existing[1] or existing[2] or "").strip()
                if prior_display and not looks_like_email(prior_display):
                    store["display_name"] = prior_display
                prior_shop = str(existing[2] or "").strip()
                if prior_shop and not looks_like_email(prior_shop):
                    store["store_name"] = prior_shop
                store["store_id"] = matched_store_id

            payload = (
                workspace_id,
                store["store_id"],
                store.get("display_name") or store.get("store_name") or "",
                store.get("store_name") or store.get("display_name") or "",
                store.get("account", ""),
                store.get("seller_id", ""),
                str(store.get("user_id") or "") or None,
                store.get("country", ""),
                store.get("account_platform", ""),
                json.dumps(store.get("country_user_info") or []),
                encrypt_secret(str(store.get("access_token", ""))),
                encrypt_secret(str(store.get("refresh_token", ""))),
                store.get("expires_in"),
                store.get("refresh_expires_in"),
                _parse_ts(store.get("access_token_expires_at")),
                _parse_ts(store.get("refresh_token_expires_at")),
                _parse_ts(store.get("authorized_at")),
                store.get("request_id"),
            )
            conn.execute(
                """
                INSERT INTO daraz_stores (
                    workspace_id, store_id, display_name, store_name, account, seller_id,
                    daraz_user_id, country, account_platform, country_user_info,
                    access_token_enc, refresh_token_enc, expires_in, refresh_expires_in,
                    access_token_expires_at, refresh_token_expires_at, authorized_at, request_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (workspace_id, store_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    store_name = EXCLUDED.store_name,
                    account = EXCLUDED.account,
                    seller_id = EXCLUDED.seller_id,
                    daraz_user_id = EXCLUDED.daraz_user_id,
                    country = EXCLUDED.country,
                    account_platform = EXCLUDED.account_platform,
                    country_user_info = EXCLUDED.country_user_info,
                    access_token_enc = EXCLUDED.access_token_enc,
                    refresh_token_enc = EXCLUDED.refresh_token_enc,
                    expires_in = EXCLUDED.expires_in,
                    refresh_expires_in = EXCLUDED.refresh_expires_in,
                    access_token_expires_at = EXCLUDED.access_token_expires_at,
                    refresh_token_expires_at = EXCLUDED.refresh_token_expires_at,
                    authorized_at = EXCLUDED.authorized_at,
                    request_id = EXCLUDED.request_id,
                    updated_at = NOW()
                """,
                payload,
            )
            conn.commit()
        found = self.get_store(workspace_id, store["store_id"])
        assert found is not None
        return found

    def update_store_display_name(
        self, workspace_id: str, store_id: str, display_name: str
    ) -> dict[str, Any]:
        from src.db.connection import connect

        name = display_name.strip()
        if not name:
            raise ValueError("Store name cannot be empty")
        with connect() as conn:
            cur = conn.execute(
                """
                UPDATE daraz_stores
                SET display_name = %s, updated_at = NOW()
                WHERE workspace_id = %s AND LOWER(store_id) = LOWER(%s)
                """,
                (name, workspace_id, store_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Unknown store: {store_id}")
            conn.commit()
        found = self.get_store(workspace_id, store_id)
        assert found is not None
        return found

    def list_groups(self, workspace_id: str) -> list[dict[str, Any]]:
        from src.db.connection import connect

        with connect() as conn:
            groups = conn.execute(
                """
                SELECT id, name, created_at, updated_at
                FROM store_groups
                WHERE workspace_id = %s
                ORDER BY name ASC
                """,
                (workspace_id,),
            ).fetchall()
            out = []
            for g in groups:
                members = conn.execute(
                    "SELECT store_id FROM store_group_members WHERE group_id = %s",
                    (g[0],),
                ).fetchall()
                out.append(
                    {
                        "id": str(g[0]),
                        "name": g[1],
                        "store_ids": [m[0] for m in members],
                        "created_at": g[2].isoformat() if g[2] else None,
                        "updated_at": g[3].isoformat() if g[3] else None,
                    }
                )
            return out

    def create_group(
        self, workspace_id: str, name: str, store_ids: list[str]
    ) -> dict[str, Any]:
        from src.db.connection import connect

        name = name.strip()
        if not name:
            raise ValueError("Group name cannot be empty")
        with connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM store_groups
                WHERE workspace_id = %s AND LOWER(name) = LOWER(%s)
                """,
                (workspace_id, name),
            ).fetchone()
            if existing:
                gid = existing[0]
                conn.execute(
                    "UPDATE store_groups SET name = %s, updated_at = NOW() WHERE id = %s",
                    (name, gid),
                )
                conn.execute("DELETE FROM store_group_members WHERE group_id = %s", (gid,))
            else:
                row = conn.execute(
                    """
                    INSERT INTO store_groups (workspace_id, name)
                    VALUES (%s, %s) RETURNING id
                    """,
                    (workspace_id, name),
                ).fetchone()
                gid = row[0]
            for sid in dict.fromkeys(store_ids):
                conn.execute(
                    """
                    INSERT INTO store_group_members (group_id, store_id)
                    VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """,
                    (gid, sid),
                )
            conn.commit()
            return {"id": str(gid), "name": name, "store_ids": list(dict.fromkeys(store_ids))}

    def update_group(
        self,
        workspace_id: str,
        group_id: str,
        *,
        name: str | None = None,
        store_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                "SELECT id, name FROM store_groups WHERE id = %s AND workspace_id = %s",
                (group_id, workspace_id),
            ).fetchone()
            if not row:
                raise ValueError("Group not found")
            new_name = row[1]
            if name is not None:
                cleaned = name.strip()
                if not cleaned:
                    raise ValueError("Group name cannot be empty")
                new_name = cleaned
                conn.execute(
                    "UPDATE store_groups SET name = %s, updated_at = NOW() WHERE id = %s",
                    (new_name, group_id),
                )
            if store_ids is not None:
                conn.execute("DELETE FROM store_group_members WHERE group_id = %s", (group_id,))
                for sid in dict.fromkeys(store_ids):
                    conn.execute(
                        "INSERT INTO store_group_members (group_id, store_id) VALUES (%s, %s)",
                        (group_id, sid),
                    )
                conn.execute(
                    "UPDATE store_groups SET updated_at = NOW() WHERE id = %s",
                    (group_id,),
                )
            members = conn.execute(
                "SELECT store_id FROM store_group_members WHERE group_id = %s",
                (group_id,),
            ).fetchall()
            conn.commit()
            return {
                "id": str(group_id),
                "name": new_name,
                "store_ids": [m[0] for m in members],
            }

    def delete_group(self, workspace_id: str, group_id: str) -> None:
        from src.db.connection import connect

        with connect() as conn:
            cur = conn.execute(
                "DELETE FROM store_groups WHERE id = %s AND workspace_id = %s",
                (group_id, workspace_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Group not found")
            conn.commit()

    def save_print_job(self, job: dict[str, Any]) -> None:
        from src.db.connection import connect

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO print_jobs (
                    id, workspace_id, user_id, status, message, error, result, output_path,
                    started_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    message = EXCLUDED.message,
                    error = EXCLUDED.error,
                    result = EXCLUDED.result,
                    output_path = EXCLUDED.output_path,
                    started_at = EXCLUDED.started_at,
                    updated_at = NOW()
                """,
                (
                    job["id"],
                    job["workspace_id"],
                    job["user_id"],
                    job["status"],
                    job.get("message") or "",
                    job.get("error"),
                    json.dumps(job.get("result")) if job.get("result") is not None else None,
                    job.get("output_path"),
                    _parse_ts(job.get("started_at")),
                ),
            )
            conn.commit()

    def get_print_job(self, job_id: str) -> dict[str, Any] | None:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                """
                SELECT id, workspace_id, user_id, status, message, error, result,
                       output_path, started_at, updated_at
                FROM print_jobs WHERE id = %s
                """,
                (job_id,),
            ).fetchone()
        if not row:
            return None
        result = row[6]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = None
        return {
            "id": str(row[0]),
            "workspace_id": str(row[1]),
            "user_id": str(row[2]),
            "status": row[3],
            "message": row[4] or "",
            "error": row[5],
            "result": result,
            "output_path": row[7],
            "started_at": row[8].isoformat() if row[8] else None,
            "updated_at": row[9].isoformat() if row[9] else None,
        }

    def list_print_jobs(
        self, workspace_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        from src.db.connection import connect

        limit = max(1, min(int(limit), 100))
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, user_id, status, message, error, result,
                       output_path, started_at, updated_at
                FROM print_jobs
                WHERE workspace_id = %s
                ORDER BY updated_at DESC NULLS LAST
                LIMIT %s
                """,
                (workspace_id, limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            result = row[6]
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    result = None
            out.append(
                {
                    "id": str(row[0]),
                    "workspace_id": str(row[1]),
                    "user_id": str(row[2]),
                    "status": row[3],
                    "message": row[4] or "",
                    "error": row[5],
                    "result": result,
                    "output_path": row[7],
                    "started_at": row[8].isoformat() if row[8] else None,
                    "updated_at": row[9].isoformat() if row[9] else None,
                }
            )
        return out

    def get_store_by_uuid(
        self, workspace_id: str, store_uuid: str
    ) -> dict[str, Any] | None:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                """
                SELECT id, workspace_id, store_id, display_name, store_name, account,
                       seller_id, daraz_user_id, country, account_platform, country_user_info,
                       access_token_enc, refresh_token_enc, expires_in, refresh_expires_in,
                       access_token_expires_at, refresh_token_expires_at, authorized_at, request_id,
                       updated_at
                FROM daraz_stores
                WHERE workspace_id = %s AND id = %s
                LIMIT 1
                """,
                (workspace_id, store_uuid),
            ).fetchone()
        if not row:
            return None
        return store_row_to_record(self._row_to_dict(row))

    def upsert_store_performance(self, **kwargs: Any) -> dict[str, Any]:
        from src.db.connection import connect

        workspace_id = str(kwargs["workspace_id"])
        store_uuid = str(kwargs["store_uuid"])
        year = int(kwargs["year"])
        month = int(kwargs["month"])
        preserve = bool(kwargs.get("preserve_counts_on_error"))

        with connect() as conn:
            if preserve and kwargs.get("sync_status") == "error":
                existing = conn.execute(
                    """
                    SELECT id FROM store_performance_monthly
                    WHERE workspace_id = %s AND store_id = %s AND year = %s AND month = %s
                    """,
                    (workspace_id, store_uuid, year, month),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE store_performance_monthly
                        SET sync_status = 'error', sync_error = %s, updated_at = NOW()
                        WHERE id = %s
                        """,
                        (kwargs.get("sync_error"), existing[0]),
                    )
                    conn.commit()
                    found = self.get_store_performance(workspace_id, store_uuid, year, month)
                    assert found is not None
                    return found

            orders_synced = bool(kwargs.get("orders_synced"))
            gross_synced = bool(kwargs.get("gross_synced"))
            conn.execute(
                """
                INSERT INTO store_performance_monthly (
                    workspace_id, store_id, year, month,
                    orders_count, gross_sales, currency,
                    previous_orders_count, orders_growth_pct,
                    previous_gross_sales, gross_sales_growth_pct,
                    source, sync_status, sync_error,
                    orders_synced_at, gross_sales_synced_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    CASE WHEN %s THEN NOW() ELSE NULL END,
                    CASE WHEN %s THEN NOW() ELSE NULL END
                )
                ON CONFLICT (store_id, year, month) DO UPDATE SET
                    workspace_id = EXCLUDED.workspace_id,
                    orders_count = EXCLUDED.orders_count,
                    gross_sales = EXCLUDED.gross_sales,
                    currency = EXCLUDED.currency,
                    previous_orders_count = EXCLUDED.previous_orders_count,
                    orders_growth_pct = EXCLUDED.orders_growth_pct,
                    previous_gross_sales = EXCLUDED.previous_gross_sales,
                    gross_sales_growth_pct = EXCLUDED.gross_sales_growth_pct,
                    source = EXCLUDED.source,
                    sync_status = EXCLUDED.sync_status,
                    sync_error = EXCLUDED.sync_error,
                    orders_synced_at = CASE
                        WHEN %s THEN NOW()
                        ELSE store_performance_monthly.orders_synced_at
                    END,
                    gross_sales_synced_at = CASE
                        WHEN %s THEN NOW()
                        ELSE store_performance_monthly.gross_sales_synced_at
                    END,
                    updated_at = NOW()
                """,
                (
                    workspace_id,
                    store_uuid,
                    year,
                    month,
                    int(kwargs.get("orders_count") or 0),
                    kwargs.get("gross_sales"),
                    kwargs.get("currency") or "PKR",
                    kwargs.get("previous_orders_count"),
                    kwargs.get("orders_growth_pct"),
                    kwargs.get("previous_gross_sales"),
                    kwargs.get("gross_sales_growth_pct"),
                    kwargs.get("source") or "orders_api",
                    kwargs.get("sync_status") or "ok",
                    kwargs.get("sync_error"),
                    orders_synced,
                    gross_synced,
                    orders_synced,
                    gross_synced,
                ),
            )
            conn.commit()
        found = self.get_store_performance(workspace_id, store_uuid, year, month)
        assert found is not None
        return found

    def get_store_performance(
        self, workspace_id: str, store_uuid: str, year: int, month: int
    ) -> dict[str, Any] | None:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                """
                SELECT id, workspace_id, store_id, year, month,
                       orders_count, gross_sales, currency,
                       previous_orders_count, orders_growth_pct,
                       previous_gross_sales, gross_sales_growth_pct,
                       source, sync_status, sync_error,
                       orders_synced_at, gross_sales_synced_at,
                       created_at, updated_at
                FROM store_performance_monthly
                WHERE workspace_id = %s AND store_id = %s AND year = %s AND month = %s
                """,
                (workspace_id, store_uuid, year, month),
            ).fetchone()
        if not row:
            return None
        return self._perf_row(row)

    def list_store_performance(
        self, workspace_id: str, year: int, month: int
    ) -> list[dict[str, Any]]:
        from src.db.connection import connect

        with connect() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, store_id, year, month,
                       orders_count, gross_sales, currency,
                       previous_orders_count, orders_growth_pct,
                       previous_gross_sales, gross_sales_growth_pct,
                       source, sync_status, sync_error,
                       orders_synced_at, gross_sales_synced_at,
                       created_at, updated_at
                FROM store_performance_monthly
                WHERE workspace_id = %s AND year = %s AND month = %s
                """,
                (workspace_id, year, month),
            ).fetchall()
        return [self._perf_row(r) for r in rows]

    def list_performance_months(self, workspace_id: str) -> list[dict[str, int]]:
        from src.db.connection import connect

        with connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT year, month
                FROM store_performance_monthly
                WHERE workspace_id = %s
                ORDER BY year DESC, month DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [{"year": int(r[0]), "month": int(r[1])} for r in rows]

    @staticmethod
    def _json_maybe(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    def _order_row(self, row) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "workspace_id": str(row[1]),
            "store_id": str(row[2]),
            "daraz_order_id": row[3],
            "order_number": row[4],
            "status_raw": row[5],
            "status_group": row[6],
            "statuses": self._json_maybe(row[7]),
            "created_at_daraz": row[8].isoformat() if row[8] else None,
            "updated_at_daraz": row[9].isoformat() if row[9] else None,
            "price": float(row[10]) if row[10] is not None else None,
            "currency": row[11],
            "items_count": int(row[12]) if row[12] is not None else None,
            "customer_first_name": row[13],
            "customer_last_name": row[14],
            "address_shipping": self._json_maybe(row[15]),
            "address_billing": self._json_maybe(row[16]),
            "payment_method": row[17],
            "shipping_fee": float(row[18]) if row[18] is not None else None,
            "warehouse_code": row[19],
            "synced_at": row[20].isoformat() if row[20] else None,
            "created_at": row[21].isoformat() if row[21] else None,
            "updated_at": row[22].isoformat() if row[22] else None,
        }

    _ORDER_SELECT = """
        id, workspace_id, store_id, daraz_order_id, order_number,
        status_raw, status_group, statuses, created_at_daraz, updated_at_daraz,
        price, currency, items_count, customer_first_name, customer_last_name,
        address_shipping, address_billing, payment_method, shipping_fee,
        warehouse_code, synced_at, created_at, updated_at
    """

    def upsert_daraz_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                f"""
                INSERT INTO daraz_orders (
                    workspace_id, store_id, daraz_order_id, order_number,
                    status_raw, status_group, statuses, created_at_daraz, updated_at_daraz,
                    price, currency, items_count, customer_first_name, customer_last_name,
                    address_shipping, address_billing, payment_method, shipping_fee,
                    warehouse_code, synced_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s, %s,
                    %s, NOW()
                )
                ON CONFLICT (store_id, daraz_order_id) DO UPDATE SET
                    order_number = EXCLUDED.order_number,
                    status_raw = EXCLUDED.status_raw,
                    status_group = EXCLUDED.status_group,
                    statuses = EXCLUDED.statuses,
                    created_at_daraz = EXCLUDED.created_at_daraz,
                    updated_at_daraz = EXCLUDED.updated_at_daraz,
                    price = EXCLUDED.price,
                    currency = EXCLUDED.currency,
                    items_count = EXCLUDED.items_count,
                    customer_first_name = EXCLUDED.customer_first_name,
                    customer_last_name = EXCLUDED.customer_last_name,
                    address_shipping = EXCLUDED.address_shipping,
                    address_billing = EXCLUDED.address_billing,
                    payment_method = EXCLUDED.payment_method,
                    shipping_fee = EXCLUDED.shipping_fee,
                    warehouse_code = EXCLUDED.warehouse_code,
                    synced_at = NOW(),
                    updated_at = NOW()
                RETURNING {self._ORDER_SELECT}
                """,
                (
                    payload["workspace_id"],
                    payload["store_id"],
                    payload["daraz_order_id"],
                    payload.get("order_number"),
                    payload.get("status_raw"),
                    payload.get("status_group") or "other",
                    json.dumps(payload.get("statuses"))
                    if payload.get("statuses") is not None
                    else None,
                    _parse_ts(payload.get("created_at_daraz")),
                    _parse_ts(payload.get("updated_at_daraz")),
                    payload.get("price"),
                    payload.get("currency"),
                    payload.get("items_count"),
                    payload.get("customer_first_name"),
                    payload.get("customer_last_name"),
                    json.dumps(payload.get("address_shipping"))
                    if payload.get("address_shipping") is not None
                    else None,
                    json.dumps(payload.get("address_billing"))
                    if payload.get("address_billing") is not None
                    else None,
                    payload.get("payment_method"),
                    payload.get("shipping_fee"),
                    payload.get("warehouse_code"),
                ),
            ).fetchone()
            conn.commit()
        return self._order_row(row)

    def upsert_daraz_order_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                """
                INSERT INTO daraz_order_items (
                    workspace_id, store_id, order_id, daraz_order_item_id, daraz_order_id,
                    status_raw, package_id, name, sku, sku_id, product_id, quantity,
                    item_price, paid_price, currency, tracking_code, shipment_provider,
                    shipping_type, warehouse_code, synced_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, NOW()
                )
                ON CONFLICT (store_id, daraz_order_item_id) DO UPDATE SET
                    order_id = EXCLUDED.order_id,
                    daraz_order_id = EXCLUDED.daraz_order_id,
                    status_raw = EXCLUDED.status_raw,
                    package_id = EXCLUDED.package_id,
                    name = EXCLUDED.name,
                    sku = EXCLUDED.sku,
                    sku_id = EXCLUDED.sku_id,
                    product_id = EXCLUDED.product_id,
                    quantity = EXCLUDED.quantity,
                    item_price = EXCLUDED.item_price,
                    paid_price = EXCLUDED.paid_price,
                    currency = EXCLUDED.currency,
                    tracking_code = EXCLUDED.tracking_code,
                    shipment_provider = EXCLUDED.shipment_provider,
                    shipping_type = EXCLUDED.shipping_type,
                    warehouse_code = EXCLUDED.warehouse_code,
                    synced_at = NOW(),
                    updated_at = NOW()
                RETURNING id, workspace_id, store_id, order_id, daraz_order_item_id,
                          daraz_order_id, status_raw, package_id, name, sku, sku_id,
                          product_id, quantity, item_price, paid_price, currency,
                          tracking_code, shipment_provider, shipping_type, warehouse_code,
                          synced_at, created_at, updated_at
                """,
                (
                    payload["workspace_id"],
                    payload["store_id"],
                    payload["order_id"],
                    payload["daraz_order_item_id"],
                    payload.get("daraz_order_id"),
                    payload.get("status_raw"),
                    payload.get("package_id"),
                    payload.get("name"),
                    payload.get("sku"),
                    payload.get("sku_id"),
                    payload.get("product_id"),
                    int(payload.get("quantity") or 1),
                    payload.get("item_price"),
                    payload.get("paid_price"),
                    payload.get("currency"),
                    payload.get("tracking_code"),
                    payload.get("shipment_provider"),
                    payload.get("shipping_type"),
                    payload.get("warehouse_code"),
                ),
            ).fetchone()
            conn.commit()
        return self._item_row(row)

    @staticmethod
    def _item_row(row) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "workspace_id": str(row[1]),
            "store_id": str(row[2]),
            "order_id": str(row[3]),
            "daraz_order_item_id": row[4],
            "daraz_order_id": row[5],
            "status_raw": row[6],
            "package_id": row[7],
            "name": row[8],
            "sku": row[9],
            "sku_id": row[10],
            "product_id": row[11],
            "quantity": int(row[12] or 1),
            "item_price": float(row[13]) if row[13] is not None else None,
            "paid_price": float(row[14]) if row[14] is not None else None,
            "currency": row[15],
            "tracking_code": row[16],
            "shipment_provider": row[17],
            "shipping_type": row[18],
            "warehouse_code": row[19],
            "synced_at": row[20].isoformat() if row[20] else None,
            "created_at": row[21].isoformat() if row[21] else None,
            "updated_at": row[22].isoformat() if row[22] else None,
        }

    def get_order_by_id(
        self, workspace_id: str, order_uuid: str
    ) -> dict[str, Any] | None:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                f"""
                SELECT {self._ORDER_SELECT}
                FROM daraz_orders
                WHERE workspace_id = %s AND id = %s
                """,
                (workspace_id, order_uuid),
            ).fetchone()
        return self._order_row(row) if row else None

    def list_orders(
        self, workspace_id: str, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        from src.db.connection import connect

        filters = filters or {}
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_size") or 50), 200))
        store_uuids = filters.get("store_uuids")
        store_slugs = filters.get("store_slugs")
        status_group = filters.get("status_group")
        status_raw = filters.get("status_raw")
        search = (filters.get("search") or "").strip()
        date_from = filters.get("date_from")
        date_to = filters.get("date_to")
        print_state = (filters.get("print_state") or "any").lower()
        sort = (filters.get("sort") or "created_at_daraz_desc").lower()

        where = ["o.workspace_id = %s"]
        params: list[Any] = [workspace_id]

        if store_uuids is not None:
            where.append("o.store_id = ANY(%s::uuid[])")
            params.append(list(store_uuids) or ["00000000-0000-0000-0000-000000000000"])
        if store_slugs is not None:
            where.append(
                "o.store_id IN (SELECT id FROM daraz_stores WHERE workspace_id = %s AND store_id = ANY(%s))"
            )
            params.extend([workspace_id, list(store_slugs) or [""]])
        if status_group:
            where.append("o.status_group = %s")
            params.append(status_group)
        if status_raw:
            where.append("o.status_raw ILIKE %s")
            params.append(f"%{status_raw}%")
        if date_from:
            where.append("o.created_at_daraz >= %s")
            params.append(_parse_ts(date_from) or date_from)
        if date_to:
            where.append("o.created_at_daraz <= %s")
            params.append(_parse_ts(date_to) or date_to)
        if print_state == "printed":
            where.append(
                "EXISTS (SELECT 1 FROM order_label_prints p WHERE p.order_id = o.id)"
            )
        elif print_state == "unprinted":
            where.append(
                "NOT EXISTS (SELECT 1 FROM order_label_prints p WHERE p.order_id = o.id)"
            )
        if search:
            where.append(
                """(
                    o.order_number ILIKE %s
                    OR o.daraz_order_id ILIKE %s
                    OR COALESCE(o.customer_first_name, '') ILIKE %s
                    OR COALESCE(o.customer_last_name, '') ILIKE %s
                    OR EXISTS (
                        SELECT 1 FROM daraz_order_items i
                        WHERE i.order_id = o.id
                          AND (COALESCE(i.sku, '') ILIKE %s OR COALESCE(i.name, '') ILIKE %s)
                    )
                )"""
            )
            like = f"%{search}%"
            params.extend([like, like, like, like, like, like])

        order_sql = "o.created_at_daraz DESC NULLS LAST"
        if sort in {"created_at_asc", "created_at_daraz_asc"}:
            order_sql = "o.created_at_daraz ASC NULLS LAST"
        elif sort in {"updated_at_desc", "updated_at_daraz_desc"}:
            order_sql = "o.updated_at_daraz DESC NULLS LAST"
        elif sort in {"updated_at_asc", "updated_at_daraz_asc"}:
            order_sql = "o.updated_at_daraz ASC NULLS LAST"

        where_sql = " AND ".join(where)
        offset = (page - 1) * page_size
        with connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM daraz_orders o WHERE {where_sql}",
                tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT o.id, o.workspace_id, o.store_id, o.daraz_order_id, o.order_number,
                       o.status_raw, o.status_group, o.statuses, o.created_at_daraz,
                       o.updated_at_daraz, o.price, o.currency, o.items_count,
                       o.customer_first_name, o.customer_last_name, o.address_shipping,
                       o.address_billing, o.payment_method, o.shipping_fee,
                       o.warehouse_code, o.synced_at, o.created_at, o.updated_at,
                       (SELECT COUNT(*)::int FROM order_label_prints p WHERE p.order_id = o.id)
                FROM daraz_orders o
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT %s OFFSET %s
                """,
                tuple(params + [page_size, offset]),
            ).fetchall()

        items = []
        for row in rows:
            item = self._order_row(row[:23])
            item["print_count"] = int(row[23] or 0)
            item["has_print"] = item["print_count"] > 0
            items.append(item)
        return {
            "items": items,
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }

    def count_orders_by_status_group(
        self, workspace_id: str, store_uuids: list[str] | None = None
    ) -> dict[str, int]:
        from src.db.connection import connect

        where = ["workspace_id = %s"]
        params: list[Any] = [workspace_id]
        if store_uuids is not None:
            where.append("store_id = ANY(%s::uuid[])")
            params.append(list(store_uuids) or ["00000000-0000-0000-0000-000000000000"])
        with connect() as conn:
            rows = conn.execute(
                f"""
                SELECT COALESCE(status_group, 'other'), COUNT(*)
                FROM daraz_orders
                WHERE {' AND '.join(where)}
                GROUP BY 1
                """,
                tuple(params),
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def list_order_items(
        self, workspace_id: str, order_uuid: str
    ) -> list[dict[str, Any]]:
        from src.db.connection import connect

        with connect() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, store_id, order_id, daraz_order_item_id,
                       daraz_order_id, status_raw, package_id, name, sku, sku_id,
                       product_id, quantity, item_price, paid_price, currency,
                       tracking_code, shipment_provider, shipping_type, warehouse_code,
                       synced_at, created_at, updated_at
                FROM daraz_order_items
                WHERE workspace_id = %s AND order_id = %s
                ORDER BY created_at ASC
                """,
                (workspace_id, order_uuid),
            ).fetchall()
        return [self._item_row(r) for r in rows]

    def list_label_prints_for_orders(
        self, workspace_id: str, order_uuids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        from src.db.connection import connect

        wanted = [str(u) for u in order_uuids]
        out: dict[str, list[dict[str, Any]]] = {u: [] for u in wanted}
        if not wanted:
            return out
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, store_id, order_id, daraz_order_id, package_id,
                       order_item_ids, print_job_id, printed_at, printed_by_user_id,
                       is_reprint, fetch_source, created_at
                FROM order_label_prints
                WHERE workspace_id = %s AND order_id = ANY(%s::uuid[])
                ORDER BY printed_at DESC
                """,
                (workspace_id, wanted),
            ).fetchall()
        for row in rows:
            item = {
                "id": str(row[0]),
                "workspace_id": str(row[1]),
                "store_id": str(row[2]),
                "order_id": str(row[3]),
                "daraz_order_id": row[4],
                "package_id": row[5],
                "order_item_ids": self._json_maybe(row[6]) or [],
                "print_job_id": str(row[7]) if row[7] else None,
                "printed_at": row[8].isoformat() if row[8] else None,
                "printed_by_user_id": str(row[9]) if row[9] else None,
                "is_reprint": bool(row[10]),
                "fetch_source": row[11],
                "created_at": row[12].isoformat() if row[12] else None,
            }
            out.setdefault(item["order_id"], []).append(item)
        return out

    def has_label_print(
        self, workspace_id: str, store_uuid: str, daraz_order_id: str
    ) -> bool:
        return self.count_label_prints(workspace_id, store_uuid, daraz_order_id) > 0

    def count_label_prints(
        self, workspace_id: str, store_uuid: str, daraz_order_id: str
    ) -> int:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM order_label_prints
                WHERE workspace_id = %s AND store_id = %s AND daraz_order_id = %s
                """,
                (workspace_id, store_uuid, daraz_order_id),
            ).fetchone()
        return int(row[0] if row else 0)

    def get_print_summary_for_order(
        self, workspace_id: str, order_uuid: str
    ) -> dict[str, Any]:
        prints = self.list_label_prints_for_orders(workspace_id, [order_uuid]).get(
            str(order_uuid), []
        )
        last = prints[0] if prints else None
        return {
            "print_count": len(prints),
            "has_print": len(prints) > 0,
            "last_printed_at": last.get("printed_at") if last else None,
            "last_is_reprint": last.get("is_reprint") if last else None,
        }

    def insert_label_print(self, payload: dict[str, Any]) -> dict[str, Any]:
        from src.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                """
                INSERT INTO order_label_prints (
                    workspace_id, store_id, order_id, daraz_order_id, package_id,
                    order_item_ids, print_job_id, printed_by_user_id, is_reprint, fetch_source
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s::jsonb, %s, %s, %s, %s
                )
                RETURNING id, workspace_id, store_id, order_id, daraz_order_id, package_id,
                          order_item_ids, print_job_id, printed_at, printed_by_user_id,
                          is_reprint, fetch_source, created_at
                """,
                (
                    payload["workspace_id"],
                    payload["store_id"],
                    payload["order_id"],
                    payload["daraz_order_id"],
                    payload.get("package_id"),
                    json.dumps(list(payload.get("order_item_ids") or [])),
                    payload.get("print_job_id"),
                    payload.get("printed_by_user_id"),
                    bool(payload.get("is_reprint")),
                    payload.get("fetch_source"),
                ),
            ).fetchone()
            conn.commit()
        return {
            "id": str(row[0]),
            "workspace_id": str(row[1]),
            "store_id": str(row[2]),
            "order_id": str(row[3]),
            "daraz_order_id": row[4],
            "package_id": row[5],
            "order_item_ids": self._json_maybe(row[6]) or [],
            "print_job_id": str(row[7]) if row[7] else None,
            "printed_at": row[8].isoformat() if row[8] else None,
            "printed_by_user_id": str(row[9]) if row[9] else None,
            "is_reprint": bool(row[10]),
            "fetch_source": row[11],
            "created_at": row[12].isoformat() if row[12] else None,
        }

    @staticmethod
    def _perf_row(row) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "workspace_id": str(row[1]),
            "store_id": str(row[2]),
            "year": int(row[3]),
            "month": int(row[4]),
            "orders_count": int(row[5] or 0),
            "gross_sales": float(row[6]) if row[6] is not None else None,
            "currency": row[7] or "PKR",
            "previous_orders_count": int(row[8]) if row[8] is not None else None,
            "orders_growth_pct": float(row[9]) if row[9] is not None else None,
            "previous_gross_sales": float(row[10]) if row[10] is not None else None,
            "gross_sales_growth_pct": float(row[11]) if row[11] is not None else None,
            "source": row[12],
            "sync_status": row[13],
            "sync_error": row[14],
            "orders_synced_at": row[15].isoformat() if row[15] else None,
            "gross_sales_synced_at": row[16].isoformat() if row[16] else None,
            "created_at": row[17].isoformat() if row[17] else None,
            "updated_at": row[18].isoformat() if row[18] else None,
        }


_repo: TenancyRepo | None = None
_repo_lock = threading.Lock()


def get_repo() -> TenancyRepo:
    global _repo
    with _repo_lock:
        if _repo is not None:
            return _repo
        # Prefer memory when AUTH_TEST_MODE or TENANCY_REPO=memory
        mode = get_env("TENANCY_REPO", "").lower()
        test_mode = get_env("AUTH_TEST_MODE", "").lower() in {"1", "true", "yes"}
        if mode == "memory" or test_mode or not get_env("DATABASE_URL"):
            # If DATABASE_URL missing but not test mode, still allow memory for
            # unit tests; production app routes will require auth config separately.
            _repo = MemoryTenancyRepo()
            return _repo
        _repo = PostgresTenancyRepo()
        return _repo


def reset_repo_for_tests(repo: TenancyRepo | None = None) -> MemoryTenancyRepo:
    """Replace the global repo (tests only)."""
    global _repo
    with _repo_lock:
        mem = repo if repo is not None else MemoryTenancyRepo()
        _repo = mem  # type: ignore[assignment]
        return mem  # type: ignore[return-value]
