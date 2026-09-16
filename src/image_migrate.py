"""Daraz image migration service — migrate external/CDN URLs to platform hosts.

Phase 4A: POST /images/migrate accepts XML and returns batch_id.
Phase 4B: poll /image/response/get until final URL or timeout/failure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.daraz_api import DarazApiError, DarazClient

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_S = 1.5
DEFAULT_TIMEOUT_S = 45.0


@dataclass
class ImageMigrationResult:
    source_url: str
    status: str  # submitted | processing | completed | failed | timeout | skipped
    batch_id: str | None = None
    migrated_url: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _extract_urls_from_response(payload: dict[str, Any]) -> list[str]:
    """Best-effort parse of migrate / response payloads for image URLs."""
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
                elif lk in {"images", "data", "image_list", "errors"}:
                    walk(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str) and node.startswith("http") and (
            "slatic" in node or "daraz" in node or "lazada" in node or "alicdn" in node
        ):
            found.append(node)

    walk(payload)
    # de-dupe preserve order
    return list(dict.fromkeys(found))


class DarazImageMigrationService:
    """Reusable migrate + poll helper. Does not create products."""

    def __init__(
        self,
        client: DarazClient,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.poll_interval_s = poll_interval_s
        self.timeout_s = timeout_s
        self.sleep_fn = sleep_fn
        self._cache: dict[str, str] = {}

    def migrate_one(self, source_url: str) -> ImageMigrationResult:
        url = (source_url or "").strip()
        if not url:
            return ImageMigrationResult(
                source_url=url, status="failed", error="empty_url"
            )
        if url in self._cache:
            return ImageMigrationResult(
                source_url=url,
                status="completed",
                migrated_url=self._cache[url],
            )

        try:
            submitted = self.client.migrate_images([url])
        except DarazApiError as exc:
            return ImageMigrationResult(
                source_url=url,
                status="failed",
                error=f"migrate:{exc.code}:{exc}",
                raw=exc.payload or {},
            )
        except Exception as exc:  # noqa: BLE001
            return ImageMigrationResult(
                source_url=url,
                status="failed",
                error=f"migrate:{type(exc).__name__}:{exc}",
            )

        batch_id = (
            submitted.get("batch_id")
            or (submitted.get("data") or {}).get("batch_id")
            if isinstance(submitted.get("data"), dict)
            else submitted.get("batch_id")
        )
        # Some platforms return URLs immediately
        immediate = _extract_urls_from_response(submitted)
        if immediate and not batch_id:
            migrated = immediate[0]
            self._cache[url] = migrated
            return ImageMigrationResult(
                source_url=url,
                status="completed",
                migrated_url=migrated,
                raw=submitted,
            )

        if not batch_id:
            # If source is already on Daraz CDN, treat as usable (migration may be no-op)
            if any(h in url for h in ("static-01.daraz", "slatic.net", "daraz.pk/p/")):
                self._cache[url] = url
                return ImageMigrationResult(
                    source_url=url,
                    status="completed",
                    migrated_url=url,
                    raw=submitted,
                    error="no_batch_id_reused_source",
                )
            return ImageMigrationResult(
                source_url=url,
                status="failed",
                error="no_batch_id",
                raw=submitted,
            )

        return self._poll(url, str(batch_id), submitted)

    def migrate_many(self, urls: list[str]) -> list[ImageMigrationResult]:
        results: list[ImageMigrationResult] = []
        seen: set[str] = set()
        for url in urls:
            key = url.strip()
            if not key or key in seen:
                if key in seen and key in self._cache:
                    results.append(
                        ImageMigrationResult(
                            source_url=key,
                            status="completed",
                            migrated_url=self._cache[key],
                        )
                    )
                continue
            seen.add(key)
            results.append(self.migrate_one(key))
        return results

    def _poll(
        self, source_url: str, batch_id: str, submitted: dict[str, Any]
    ) -> ImageMigrationResult:
        deadline = time.monotonic() + self.timeout_s
        last_payload: dict[str, Any] = {}
        last_error: str | None = None
        while time.monotonic() < deadline:
            try:
                resp = self.client.get_image_response(batch_id)
                last_payload = resp if isinstance(resp, dict) else {}
                urls = _extract_urls_from_response(last_payload)
                # Lazada often nests images under data
                data = last_payload.get("data")
                if isinstance(data, dict) and data.get("errors"):
                    return ImageMigrationResult(
                        source_url=source_url,
                        status="failed",
                        batch_id=batch_id,
                        error=str(data.get("errors"))[:240],
                        raw=last_payload,
                    )
                if urls:
                    migrated = urls[0]
                    self._cache[source_url] = migrated
                    return ImageMigrationResult(
                        source_url=source_url,
                        status="completed",
                        batch_id=batch_id,
                        migrated_url=migrated,
                        raw=last_payload,
                    )
                # Still processing
                self.sleep_fn(self.poll_interval_s)
                continue
            except DarazApiError as exc:
                last_error = f"{exc.code}:{exc}"
                last_payload = exc.payload or {}
                # E208 empty / E005 format — keep polling briefly; may be eventual
                code = str(exc.code or "")
                if code in {"208", "E208", "5", "E005"}:
                    self.sleep_fn(self.poll_interval_s)
                    continue
                return ImageMigrationResult(
                    source_url=source_url,
                    status="failed",
                    batch_id=batch_id,
                    error=last_error,
                    raw=last_payload,
                )
            except Exception as exc:  # noqa: BLE001
                return ImageMigrationResult(
                    source_url=source_url,
                    status="failed",
                    batch_id=batch_id,
                    error=f"{type(exc).__name__}:{exc}",
                )

        # Timeout fallback: if source already Daraz-hosted, allow reuse with warning
        if any(h in source_url for h in ("static-01.daraz", "slatic.net")):
            self._cache[source_url] = source_url
            return ImageMigrationResult(
                source_url=source_url,
                status="timeout",
                batch_id=batch_id,
                migrated_url=source_url,
                error=last_error or "poll_timeout_reused_source_cdn",
                raw=last_payload or submitted,
            )

        return ImageMigrationResult(
            source_url=source_url,
            status="timeout",
            batch_id=batch_id,
            error=last_error or "poll_timeout",
            raw=last_payload or submitted,
        )
