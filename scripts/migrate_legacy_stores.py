#!/usr/bin/env python3
"""
Bootstrap: import legacy single-tenant stores into one owner's workspace.

Usage:
  set ALLOW_LEGACY_DATA_IMPORT=true
  set SUPABASE_URL=...
  # (JWT verified via JWKS; AUTH_TEST_MODE for local unit tests)
  set DATABASE_URL=...   # optional; memory repo used if unset / AUTH_TEST_MODE

  python -m scripts.migrate_legacy_stores --workspace-id <UUID>

Or via API (owner only):
  POST /api/admin/import-legacy-stores  {"confirm": true}
  Header: Authorization: Bearer <supabase_access_token>
  Header: X-Workspace-Id: <workspace_id>

IMPORTANT:
  - Does NOT delete data/tokens.json or the daraz_app_kv stores_v1 blob.
  - Creates a timestamped backup under data/backups/ when possible.
  - Idempotent: safe to re-run for the same workspace.
  - Do NOT point this at an arbitrary new account — use the first owner's workspace.
"""

from __future__ import annotations

import argparse
import sys

from src.config import get_env
from src.legacy_import import import_legacy_into_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import legacy Daraz stores into a workspace")
    parser.add_argument("--workspace-id", required=True, help="Target workspace UUID")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip ALLOW_LEGACY_DATA_IMPORT check (local recovery only)",
    )
    args = parser.parse_args(argv)

    if not args.force and get_env("ALLOW_LEGACY_DATA_IMPORT", "").lower() not in {
        "1",
        "true",
        "yes",
    }:
        print(
            "Refusing to run: set ALLOW_LEGACY_DATA_IMPORT=true "
            "(or pass --force for local recovery).",
            file=sys.stderr,
        )
        return 2

    result = import_legacy_into_workspace(args.workspace_id)
    print(f"Imported {result['imported_count']} store(s) into {result['workspace_id']}")
    if result.get("backup"):
        print(f"Backup: {result['backup']}")
    for row in result.get("stores") or []:
        print(f"  - {row.get('store_id')} ({row.get('account')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
