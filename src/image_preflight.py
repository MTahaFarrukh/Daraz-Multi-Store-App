"""Dry-run image preflight — public HTTP reads only; never MigrateImage."""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from src.image_migrate import is_daraz_product_cdn_url

_MAX_BYTES = 1_000_000  # 1MB Lazada/Daraz migrate limit
_MAX_PER_CALL = 8
_ALLOWED_CT = ("image/jpeg", "image/jpg", "image/png")
_ALLOWED_EXT = (".jpg", ".jpeg", ".png")


def _looks_jpg_png(url: str, content_type: str | None) -> bool:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _ALLOWED_CT:
        return True
    path = (urlparse(url).path or "").lower()
    return any(path.endswith(ext) for ext in _ALLOWED_EXT)


def preflight_images_for_dry_run(
    urls: list[str],
    *,
    fetch: Callable[[str], dict[str, Any]] | None = None,
    max_bytes: int = _MAX_BYTES,
    max_per_migrate_call: int = _MAX_PER_CALL,
) -> dict[str, Any]:
    """Validate non-CDN image URLs for migrate eligibility without calling MigrateImage.

    ``fetch(url)`` may return ``{ok, content_type, content_length, error}``.
    Default uses public HTTP HEAD then ranged GET — never Daraz mutate APIs.
    """

    def _default_fetch(url: str) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True) as client:
                head = client.head(url)
                ct = head.headers.get("content-type")
                cl = head.headers.get("content-length")
                if head.status_code >= 400 or cl is None:
                    # Fall back to tiny ranged GET for metadata only
                    get = client.get(url, headers={"Range": "bytes=0-0"})
                    ct = ct or get.headers.get("content-type")
                    cr = get.headers.get("content-range") or ""
                    if "/" in cr:
                        try:
                            cl = cr.rsplit("/", 1)[-1]
                        except Exception:  # noqa: BLE001
                            cl = cl
                    if cl is None:
                        cl = get.headers.get("content-length")
                    if get.status_code >= 400 and head.status_code >= 400:
                        return {
                            "ok": False,
                            "content_type": ct,
                            "content_length": int(cl) if cl and str(cl).isdigit() else None,
                            "error": f"http_{get.status_code or head.status_code}",
                        }
                length = None
                if cl is not None and str(cl).isdigit():
                    length = int(cl)
                return {
                    "ok": True,
                    "content_type": ct,
                    "content_length": length,
                    "error": None,
                }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "content_type": None,
                "content_length": None,
                "error": type(exc).__name__,
            }

    fetch_fn = fetch or _default_fetch
    cleaned = [str(u).strip() for u in urls if u and str(u).strip()]
    unique = list(dict.fromkeys(cleaned))
    issues: list[dict[str, Any]] = []
    non_cdn = [u for u in unique if not is_daraz_product_cdn_url(u)]
    cdn = [u for u in unique if is_daraz_product_cdn_url(u)]

    if len(non_cdn) > max_per_migrate_call:
        issues.append(
            {
                "url": None,
                "issue": "migrate_batch_count",
                "message": (
                    f"{len(non_cdn)} non-CDN URLs exceed max {max_per_migrate_call} "
                    "per MigrateImages call"
                ),
                "count": len(non_cdn),
            }
        )

    for url in non_cdn:
        meta = fetch_fn(url)
        ct = meta.get("content_type")
        length = meta.get("content_length")
        if not meta.get("ok"):
            issues.append(
                {
                    "url": url,
                    "issue": "fetch_failed",
                    "message": str(meta.get("error") or "fetch_failed"),
                    "content_type": ct,
                    "content_length": length,
                }
            )
            continue
        if not _looks_jpg_png(url, ct if isinstance(ct, str) else None):
            issues.append(
                {
                    "url": url,
                    "issue": "format",
                    "message": "Only JPG/PNG accepted for MigrateImages",
                    "content_type": ct,
                    "content_length": length,
                }
            )
        if length is not None and int(length) > max_bytes:
            issues.append(
                {
                    "url": url,
                    "issue": "size",
                    "message": f"Image exceeds {max_bytes} bytes",
                    "content_type": ct,
                    "content_length": length,
                }
            )
        if length is None:
            issues.append(
                {
                    "url": url,
                    "issue": "size_unknown",
                    "message": "Content-Length unavailable — size not verified",
                    "content_type": ct,
                    "content_length": None,
                }
            )

    return {
        "status": "OK" if not issues else "ISSUES",
        "mutating_calls": 0,
        "total_urls": len(unique),
        "cdn_urls": len(cdn),
        "non_cdn_urls": len(non_cdn),
        "max_bytes": max_bytes,
        "max_per_migrate_call": max_per_migrate_call,
        "issues": issues,
    }
