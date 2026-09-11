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
                    store["store_name"] = prior_display
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
            row["store_name"] = name
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
                       access_token_expires_at, refresh_token_expires_at, authorized_at, request_id
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
                   access_token_expires_at, refresh_token_expires_at, authorized_at, request_id
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
                    store["store_name"] = prior_display
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
                SET display_name = %s, store_name = %s, updated_at = NOW()
                WHERE workspace_id = %s AND LOWER(store_id) = LOWER(%s)
                """,
                (name, name, workspace_id, store_id),
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
