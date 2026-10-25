"""Fail-closed enforcement of trusted-connection copy_products on create/copy paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from src.db import reset_repo_for_tests
from src.workspace_connections import (
    accept_connection,
    assert_product_copy_allowed,
    connection_allows_copy,
    ensure_workspace_connection_code,
    evaluate_product_copy_permission,
    request_connection,
    update_permissions,
)


@pytest.fixture()
def tenancy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / ".token_key"
    key_path.write_text(key, encoding="utf-8")
    monkeypatch.setenv("AUTH_TEST_MODE", "true")
    monkeypatch.setenv("TENANCY_REPO", "memory")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test_key")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test_key_server_only")
    monkeypatch.setenv("DARAZ_APP_KEY", "appkey")
    monkeypatch.setenv("DARAZ_APP_SECRET", "appsecret")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE", "true")
    monkeypatch.setenv("ALLOW_PRODUCT_CREATE_PROBE", "false")
    monkeypatch.setattr("src.crypto_tokens.TOKEN_KEY_PATH", key_path)
    monkeypatch.setattr("src.token_store.TOKEN_KEY_PATH", key_path)
    return reset_repo_for_tests()


def _two_workspaces(tenancy_env):
    a = tenancy_env.create_workspace_with_owner("u-perm-a", "Perm A")["workspace"]["id"]
    b = tenancy_env.create_workspace_with_owner("u-perm-b", "Perm B")["workspace"]["id"]
    ensure_workspace_connection_code(a)
    code_b = ensure_workspace_connection_code(b)
    return a, b, code_b


def test_same_workspace_copy_allowed_without_connection(tenancy_env):
    a, _, _ = _two_workspaces(tenancy_env)
    gate = evaluate_product_copy_permission(
        actor_workspace_id=a,
        source_workspace_id=a,
        destination_workspace_id=a,
    )
    assert gate["allowed"] is True
    assert gate["code"] == "same_workspace"


def test_public_url_style_missing_source_treated_as_actor(tenancy_env):
    a, _, _ = _two_workspaces(tenancy_env)
    gate = evaluate_product_copy_permission(
        actor_workspace_id=a,
        source_workspace_id=None,
        destination_workspace_id=a,
    )
    assert gate["allowed"] is True


def test_cross_workspace_denied_without_connection(tenancy_env):
    a, b, _ = _two_workspaces(tenancy_env)
    gate = evaluate_product_copy_permission(
        actor_workspace_id=a,
        source_workspace_id=b,
        destination_workspace_id=a,
    )
    assert gate["allowed"] is False
    assert gate["code"] == "copy_permission_denied"
    assert connection_allows_copy(a, b) is False


def test_accepted_connection_without_copy_products_denied(tenancy_env):
    a, b, code_b = _two_workspaces(tenancy_env)
    row = request_connection(
        source_workspace_id=a, actor_user_id="u-perm-a", target_connection_code=code_b
    )
    accept_connection(workspace_id=b, connection_id=row["id"], actor_user_id="u-perm-b")
    assert connection_allows_copy(a, b) is False

    gate = evaluate_product_copy_permission(
        actor_workspace_id=a,
        source_workspace_id=b,
        destination_workspace_id=a,
    )
    assert gate["allowed"] is False
    assert gate["reason"] == "copy_products_not_granted"


def test_accepted_connection_with_copy_products_allowed(tenancy_env):
    a, b, code_b = _two_workspaces(tenancy_env)
    row = request_connection(
        source_workspace_id=a, actor_user_id="u-perm-a", target_connection_code=code_b
    )
    accept_connection(workspace_id=b, connection_id=row["id"], actor_user_id="u-perm-b")
    update_permissions(
        workspace_id=b,
        connection_id=row["id"],
        actor_user_id="u-perm-b",
        copy_products=True,
    )
    assert connection_allows_copy(a, b) is True
    gate = evaluate_product_copy_permission(
        actor_workspace_id=a,
        source_workspace_id=b,
        destination_workspace_id=a,
    )
    assert gate["allowed"] is True
    assert gate["code"] == "connection_granted"


def test_ambiguous_copy_products_flag_fails_closed(tenancy_env):
    a, b, code_b = _two_workspaces(tenancy_env)
    row = request_connection(
        source_workspace_id=a, actor_user_id="u-perm-a", target_connection_code=code_b
    )
    accept_connection(workspace_id=b, connection_id=row["id"], actor_user_id="u-perm-b")
    tenancy_env.update_connection(row["id"], copy_products="maybe")
    assert connection_allows_copy(a, b) is False


def test_destination_outside_actor_denied(tenancy_env):
    a, b, _ = _two_workspaces(tenancy_env)
    gate = evaluate_product_copy_permission(
        actor_workspace_id=a,
        source_workspace_id=a,
        destination_workspace_id=b,
    )
    assert gate["allowed"] is False
    assert gate["reason"] == "destination_outside_actor_workspace"


def test_prepare_destination_blocks_disallowed_peer_source(tenancy_env):
    """Create/copy destination prep must not proceed when peer copy is denied."""
    from src.product_add import _prepare_destination

    a, b, code_b = _two_workspaces(tenancy_env)
    row = request_connection(
        source_workspace_id=a, actor_user_id="u-perm-a", target_connection_code=code_b
    )
    accept_connection(workspace_id=b, connection_id=row["id"], actor_user_id="u-perm-b")
    # copy_products remains False — fail closed

    dest = {
        "id": "dest-uuid",
        "store_id": "dest_store",
        "workspace_id": a,
        "display_name": "Dest",
        "country": "pk",
    }
    draft = {
        "source_workspace_id": b,
        "product": {"title": "x", "primary_category_id": 1},
        "variants": [],
        "media": {},
    }
    out = _prepare_destination(
        a,
        draft=draft,
        dest=dest,
        price_override=None,
        allow_duplicates=False,
        execute=True,
        confirm=True,
        gate_on=True,
    )
    assert out["status"] == "NEEDS_ATTENTION"
    assert out["code"] == "copy_permission_denied"
    assert out["reason"] == "copy_products_not_granted"


def test_bulk_start_raises_when_resolved_stores_are_peer_disallowed(
    tenancy_env, monkeypatch
):
    from src.product_bulk_copy import start_bulk_copy_job

    a, b, code_b = _two_workspaces(tenancy_env)
    row = request_connection(
        source_workspace_id=a, actor_user_id="u-perm-a", target_connection_code=code_b
    )
    accept_connection(workspace_id=b, connection_id=row["id"], actor_user_id="u-perm-b")

    peer_source = {
        "id": "src-uuid",
        "store_id": "src_store",
        "workspace_id": b,
        "display_name": "Peer Source",
    }
    local_dest = {
        "id": "dst-uuid",
        "store_id": "dst_store",
        "workspace_id": a,
        "display_name": "Local Dest",
    }

    def _fake_resolve(_wid: str, ref: str):
        if "src" in str(ref):
            return peer_source
        return local_dest

    monkeypatch.setattr("src.product_bulk_copy._resolve_store", _fake_resolve)

    with pytest.raises(PermissionError, match="copy_permission_denied"):
        start_bulk_copy_job(
            a,
            source_store_id="src_store",
            destination_store_id="dst_store",
            product_item_ids=["1"],
            spawn_worker=False,
            refresh_catalog=False,
        )


def test_assert_product_copy_allowed_from_store_records(tenancy_env):
    a, b, _ = _two_workspaces(tenancy_env)
    denied = assert_product_copy_allowed(
        actor_workspace_id=a,
        source_store={"workspace_id": b},
        destination_store={"workspace_id": a},
    )
    assert denied["allowed"] is False
    allowed = assert_product_copy_allowed(
        actor_workspace_id=a,
        source_store={"workspace_id": a},
        destination_store={"workspace_id": a},
    )
    assert allowed["allowed"] is True
