"""Daraz image migration — CDN reuse, singular migrate, batch migrate + poll.

Phase 4C live proof (PK):
- POST /images/migrate with ``<Images><Url>…</Url></Images>`` → batch_id
- GET /image/response/get?batch_id=… → data.images[].url
- POST /images/migrate with singular ``<Image>`` → batch_id that polls as E005
- POST /image/migrate with ``<Image>`` → immediate data.image.url
- Own ``static-01.daraz.pk`` URLs are reusable without migrate for drafts;
  singular migrate returns the same CDN URL (identity).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from src.daraz_api import DarazApiError, DarazClient

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S = 1.0
DEFAULT_TIMEOUT_S = 45.0
DEFAULT_INITIAL_WAIT_S = 0.6

_DARAZ_CDN_HOST_MARKERS = (
    "static-01.daraz",
    "static-02.daraz",
    "daraz.pk/p/",
    "slatic.net",
    "lazcdn.com",
    "lazada.",
)


@dataclass
class ImageMigrationResult:
    source_url: str
    status: str  # completed | processing | failed | timeout | skipped
    strategy: str = ""  # reuse_cdn | singular_migrate | batch_migrate
    batch_id: str | None = None
    migrated_url: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def is_daraz_product_cdn_url(url: str) -> bool:
    """True when URL looks like a Daraz/Lazada product CDN host."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    try:
        host = (urlparse(u).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    lowered = u.lower()
    if any(m in lowered for m in _DARAZ_CDN_HOST_MARKERS):
        return True
    return host.endswith(".daraz.pk") or host.endswith(".daraz.com")


def _extract_urls_from_response(payload: dict[str, Any]) -> list[str]:
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                lk = str(k).lower()
                if lk in {"url", "image", "image_url", "migrated_url", "new_url"} and isinstance(
                    v, str
                ):
                    if v.startswith("http"):
                        found.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return list(dict.fromkeys(found))


def _batch_id_from_migrate(payload: dict[str, Any]) -> str | None:
    if payload.get("batch_id"):
        return str(payload["batch_id"])
    data = payload.get("data")
    if isinstance(data, dict) and data.get("batch_id"):
        return str(data["batch_id"])
    return None


class DarazImageMigrationService:
    """Resolve image URLs for CreateProduct.

    Prefer direct reuse of already-valid Daraz CDN URLs.
    External / non-compatible URLs go through migrate (+ poll for batch).
    """

    def __init__(
        self,
        client: DarazClient,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        initial_wait_s: float = DEFAULT_INITIAL_WAIT_S,
        sleep_fn: Callable[[float], None] = time.sleep,
        prefer_cdn_reuse: bool = True,
    ) -> None:
        self.client = client
        self.poll_interval_s = poll_interval_s
        self.timeout_s = timeout_s
        self.initial_wait_s = initial_wait_s
        self.sleep_fn = sleep_fn
        self.prefer_cdn_reuse = prefer_cdn_reuse
        self._cache: dict[str, str] = {}

    def resolve_one(self, source_url: str, *, force_migrate: bool = False) -> ImageMigrationResult:
        """Resolve a single URL to a CreateProduct-usable image URL."""
        url = (source_url or "").strip()
        if not url:
            return ImageMigrationResult(
                source_url=url, status="failed", error="empty_url", strategy="none"
            )
        if url in self._cache:
            return ImageMigrationResult(
                source_url=url,
                status="completed",
                strategy="cache",
                migrated_url=self._cache[url],
            )

        if self.prefer_cdn_reuse and not force_migrate and is_daraz_product_cdn_url(url):
            self._cache[url] = url
            return ImageMigrationResult(
                source_url=url,
                status="completed",
                strategy="reuse_cdn",
                migrated_url=url,
            )

        # Prefer singular migrate for one URL (immediate URL, no poll).
        try:
            submitted = self.client.migrate_image(url)
        except DarazApiError as exc:
            # Fall back to batch migrate + poll
            return self._batch_migrate_one(url, prior_error=f"singular:{exc.code}:{exc}")
        except Exception as exc:  # noqa: BLE001
            return self._batch_migrate_one(
                url, prior_error=f"singular:{type(exc).__name__}:{exc}"
            )

        data = submitted.get("data") if isinstance(submitted.get("data"), dict) else {}
        image = data.get("image") if isinstance(data, dict) else None
        immediate = None
        if isinstance(image, dict) and image.get("url"):
            immediate = str(image["url"])
        if not immediate:
            urls = _extract_urls_from_response(submitted)
            immediate = urls[0] if urls else None
        if immediate:
            self._cache[url] = immediate
            return ImageMigrationResult(
                source_url=url,
                status="completed",
                strategy="singular_migrate",
                migrated_url=immediate,
                raw=submitted,
            )
        return self._batch_migrate_one(url, prior_error="singular_no_url")

    def migrate_one(self, source_url: str) -> ImageMigrationResult:
        """Force migrate path (no CDN short-circuit). Kept for tests/scripts."""
        return self.resolve_one(source_url, force_migrate=True)

    def resolve_many(self, urls: list[str]) -> list[ImageMigrationResult]:
        results: list[ImageMigrationResult] = []
        seen: set[str] = set()
        for url in urls:
            key = (url or "").strip()
            if not key:
                continue
            if key in seen:
                if key in self._cache:
                    results.append(
                        ImageMigrationResult(
                            source_url=key,
                            status="completed",
                            strategy="cache",
                            migrated_url=self._cache[key],
                        )
                    )
                continue
            seen.add(key)
            results.append(self.resolve_one(key))
        return results

    def migrate_many(self, urls: list[str]) -> list[ImageMigrationResult]:
        return [self.resolve_one(u, force_migrate=True) for u in urls if (u or "").strip()]

    def _batch_migrate_one(
        self, url: str, *, prior_error: str | None = None
    ) -> ImageMigrationResult:
        try:
            submitted = self.client.migrate_images([url])
        except DarazApiError as exc:
            return ImageMigrationResult(
                source_url=url,
                status="failed",
                strategy="batch_migrate",
                error=f"migrate:{exc.code}:{exc}"
                + (f"; prior={prior_error}" if prior_error else ""),
                raw=exc.payload or {},
            )
        except Exception as exc:  # noqa: BLE001
            return ImageMigrationResult(
                source_url=url,
                status="failed",
                strategy="batch_migrate",
                error=f"migrate:{type(exc).__name__}:{exc}"
                + (f"; prior={prior_error}" if prior_error else ""),
            )

        batch_id = _batch_id_from_migrate(submitted)
        if not batch_id:
            return ImageMigrationResult(
                source_url=url,
                status="failed",
                strategy="batch_migrate",
                error="no_batch_id",
                raw=submitted,
            )
        return self._poll(url, batch_id, submitted)

    def _poll(
        self, source_url: str, batch_id: str, submitted: dict[str, Any]
    ) -> ImageMigrationResult:
        if self.initial_wait_s > 0:
            self.sleep_fn(self.initial_wait_s)

        deadline = time.monotonic() + self.timeout_s
        last_payload: dict[str, Any] = {}
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                resp = self.client.get_image_response(batch_id)
                last_payload = resp if isinstance(resp, dict) else {}
                data = last_payload.get("data")
                if isinstance(data, dict) and data.get("errors"):
                    return ImageMigrationResult(
                        source_url=source_url,
                        status="failed",
                        strategy="batch_migrate",
                        batch_id=batch_id,
                        error=str(data.get("errors"))[:240],
                        raw=last_payload,
                    )
                urls = _extract_urls_from_response(last_payload)
                if urls:
                    migrated = urls[0]
                    self._cache[source_url] = migrated
                    return ImageMigrationResult(
                        source_url=source_url,
                        status="completed",
                        strategy="batch_migrate",
                        batch_id=batch_id,
                        migrated_url=migrated,
                        raw=last_payload,
                    )
                # Still processing — empty images list
                self.sleep_fn(self.poll_interval_s)
                continue
            except DarazApiError as exc:
                last_error = f"{exc.code}:{exc}"
                last_payload = exc.payload or {}
                code = str(exc.code or "")
                # Empty / not ready yet — keep polling briefly
                if code in {"208", "E208"}:
                    self.sleep_fn(self.poll_interval_s)
                    continue
                # E005 with wrong XML batch is fatal (do not spin forever)
                if code in {"5", "E005"}:
                    return ImageMigrationResult(
                        source_url=source_url,
                        status="failed",
                        strategy="batch_migrate",
                        batch_id=batch_id,
                        error=last_error,
                        raw=last_payload,
                    )
                return ImageMigrationResult(
                    source_url=source_url,
                    status="failed",
                    strategy="batch_migrate",
                    batch_id=batch_id,
                    error=last_error,
                    raw=last_payload,
                )
            except Exception as exc:  # noqa: BLE001
                return ImageMigrationResult(
                    source_url=source_url,
                    status="failed",
                    strategy="batch_migrate",
                    batch_id=batch_id,
                    error=f"{type(exc).__name__}:{exc}",
                )

        return ImageMigrationResult(
            source_url=source_url,
            status="timeout",
            strategy="batch_migrate",
            batch_id=batch_id,
            error=last_error or "poll_timeout",
            raw=last_payload or submitted,
        )
