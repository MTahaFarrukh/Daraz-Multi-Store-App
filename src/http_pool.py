"""Shared HTTPX client pool for Product Add / Daraz API (connection reuse).

Store tokens stay on each DarazClient instance — never on the shared transport.
"""

from __future__ import annotations

import atexit
import threading
from typing import Any

import httpx

_lock = threading.Lock()
_clients: dict[str, httpx.Client] = {}


def _key(timeout: float, *, follow_redirects: bool) -> str:
    return f"{timeout}:{int(follow_redirects)}"


def get_shared_http_client(
    timeout: float = 30.0,
    *,
    follow_redirects: bool = True,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    """Return a process-wide httpx.Client (thread-safe enough for bounded pools)."""
    k = _key(timeout, follow_redirects=follow_redirects)
    with _lock:
        client = _clients.get(k)
        if client is None or client.is_closed:
            client = httpx.Client(
                timeout=timeout,
                follow_redirects=follow_redirects,
                headers=headers or {},
            )
            _clients[k] = client
        return client


def close_shared_http_clients() -> None:
    with _lock:
        for client in _clients.values():
            try:
                if not client.is_closed:
                    client.close()
            except Exception:  # noqa: BLE001
                pass
        _clients.clear()


def close_shared_http_clients_for_tests() -> None:
    close_shared_http_clients()


atexit.register(close_shared_http_clients)


def shared_client_count_for_tests() -> int:
    with _lock:
        return sum(1 for c in _clients.values() if not c.is_closed)
