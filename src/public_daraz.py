"""SSRF-safe public Daraz.pk product page extraction → ProductCloneDraft."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from src.db.repo import get_repo
from src.description_enhance import enhance_description_with_images
from src.image_migrate import is_daraz_product_cdn_url
from src.package_resolve import resolve_variant_package
from src.seller_sku import DEFAULT_SKU_PREFIX, generate_seller_sku

logger = logging.getLogger(__name__)

ALLOWED_HOST_SUFFIXES = (".daraz.pk",)
ALLOWED_HOSTS = frozenset({"daraz.pk", "www.daraz.pk"})
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 15.0
CACHE_TTL_S = 30 * 60
MAX_REDIRECTS = 5

_ITEM_RE = re.compile(r"(?:-i|/products/i)(\d{6,})", re.I)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class PublicDarazError(Exception):
    def __init__(self, message: str, *, code: str = "public_url_error"):
        super().__init__(message)
        self.code = code


def _host_allowed(host: str) -> bool:
    h = (host or "").lower().strip().rstrip(".")
    if h in ALLOWED_HOSTS:
        return True
    return any(h.endswith(suf) for suf in ALLOWED_HOST_SUFFIXES)


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _resolve_public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise PublicDarazError(f"DNS resolution failed for {host}", code="dns_failed") from exc
    for info in infos:
        ip = info[4][0]
        if not _ip_is_public(ip):
            raise PublicDarazError(
                f"Host {host} resolves to a non-public address",
                code="ssrf_blocked",
            )


def normalize_daraz_product_url(url: str) -> tuple[str, str | None]:
    raw = (url or "").strip()
    if not raw:
        raise PublicDarazError("URL is required", code="missing_url")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise PublicDarazError("Only https URLs are allowed", code="bad_scheme")
    host = (parsed.hostname or "").lower()
    if not _host_allowed(host):
        raise PublicDarazError(
            "Only daraz.pk product URLs are supported",
            code="host_not_allowed",
        )
    if host in {"localhost", "127.0.0.1"} or host.endswith(".local"):
        raise PublicDarazError("Localhost URLs are not allowed", code="ssrf_blocked")
    _resolve_public(host)
    path = parsed.path or "/"
    item_id = None
    m = _ITEM_RE.search(path) or _ITEM_RE.search(raw)
    if m:
        item_id = m.group(1)
    if not item_id:
        raise PublicDarazError(
            "URL does not look like a Daraz product page (missing item id)",
            code="not_product_url",
        )
    # Drop tracking query/fragment
    clean = urlunparse(("https", host, path, "", "", ""))
    return clean, item_id


class _JsonLdFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_ld = False
        self.blocks: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        typ = ""
        for k, v in attrs:
            if k.lower() == "type" and v:
                typ = v.lower()
        if "ld+json" in typ:
            self._in_ld = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_ld:
            self.blocks.append("".join(self._buf))
            self._in_ld = False
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._buf.append(data)


def _parse_json_ld(html: str) -> dict[str, Any]:
    finder = _JsonLdFinder()
    try:
        finder.feed(html)
        finder.close()
    except Exception:  # noqa: BLE001
        return {}
    for block in finder.blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(str(x).lower() == "product" for x in types if x):
                return obj
    return {}


def _extract_images(product_ld: dict[str, Any]) -> list[str]:
    images: list[str] = []
    raw = product_ld.get("image")
    if isinstance(raw, str):
        images.append(raw)
    elif isinstance(raw, list):
        for i in raw:
            if isinstance(i, str):
                images.append(i)
            elif isinstance(i, dict) and i.get("url"):
                images.append(str(i["url"]))
    return list(dict.fromkeys(images))


def _extract_price(product_ld: dict[str, Any]) -> float | None:
    offers = product_ld.get("offers")
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if not isinstance(offers, dict):
        return None
    price = offers.get("price") or offers.get("lowPrice")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def _extract_sku_infos_best_effort(html: str) -> list[dict[str, Any]]:
    """Best-effort variants from embedded skuInfos — never invent sale props."""
    m = re.search(r'"skuInfos"\s*:\s*(\{)', html or "")
    if not m:
        return []
    start = m.start(1)
    depth = 0
    end = None
    for i, ch in enumerate(html[start : start + 200_000], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return []
    try:
        data = json.loads(html[start:end])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for key, sku in data.items():
        if not isinstance(sku, dict):
            continue
        if str(key) == "0" and not sku.get("skuId") and len(data) > 1:
            continue
        price = sku.get("price")
        if isinstance(price, dict):
            price = price.get("price")
        try:
            price_f = float(price) if price is not None else None
        except (TypeError, ValueError):
            price_f = None
        sale: dict[str, Any] = {}
        props = sku.get("saleProp") or sku.get("properties")
        if isinstance(props, dict):
            sale = {str(k): v for k, v in props.items()}
        elif isinstance(sku.get("propPath"), str) and sku.get("propPath"):
            sale = {"propPath": sku["propPath"]}
        out.append(
            {
                "daraz_sku_id": str(sku.get("skuId") or key),
                "seller_sku": sku.get("sellerSku") or sku.get("SellerSku"),
                "price": price_f,
                "sale_props": sale,
            }
        )
    return out


def fetch_public_product(url: str, *, use_cache: bool = True) -> dict[str, Any]:
    clean, item_id = normalize_daraz_product_url(url)
    now = time.time()
    if use_cache and clean in _cache:
        ts, payload = _cache[clean]
        if now - ts < CACHE_TTL_S:
            return payload

    t0 = time.perf_counter()
    current = clean
    html = ""
    with httpx.Client(
        timeout=TIMEOUT_S,
        follow_redirects=False,
        headers={"User-Agent": "MultiStoreProductImport/1.0"},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            if parsed.scheme != "https" or not _host_allowed(parsed.hostname or ""):
                raise PublicDarazError("Redirect target not allowed", code="ssrf_blocked")
            _resolve_public(parsed.hostname or "")
            resp = client.get(current)
            if resp.status_code in {301, 302, 303, 307, 308}:
                loc = resp.headers.get("location")
                if not loc:
                    raise PublicDarazError("Redirect without Location", code="bad_redirect")
                if loc.startswith("/"):
                    loc = f"https://{parsed.hostname}{loc}"
                current = loc
                continue
            if resp.status_code >= 400:
                raise PublicDarazError(
                    f"HTTP {resp.status_code} fetching product page",
                    code="http_error",
                )
            raw = resp.content
            if len(raw) > MAX_BYTES:
                raise PublicDarazError("Response too large", code="response_too_large")
            html = raw.decode(resp.encoding or "utf-8", errors="replace")
            break
        else:
            raise PublicDarazError("Too many redirects", code="redirect_limit")

    ld = _parse_json_ld(html)
    title = str(ld.get("name") or "").strip() or None
    description = str(ld.get("description") or "").strip() or ""
    brand = None
    b = ld.get("brand")
    if isinstance(b, dict):
        brand = b.get("name")
    elif isinstance(b, str):
        brand = b
    images = _extract_images(ld)
    # Prefer Daraz CDN images only when present
    images = [u for u in images if u.startswith("http")]
    price = _extract_price(ld)
    category_hint = None
    cat = ld.get("category")
    if isinstance(cat, str):
        category_hint = cat
    elif isinstance(cat, dict):
        category_hint = cat.get("name")

    variants = _extract_sku_infos_best_effort(html)

    payload = {
        "source_type": "public_daraz_url",
        "source_url": clean,
        "item_id": item_id,
        "title": title,
        "brand": brand,
        "description_html": description,
        "images": images,
        "price": price,
        "category_hint": category_hint,
        "variants": variants,
        "provenance": {
            "title": "json-ld" if title else "unavailable",
            "images": "json-ld" if images else "unavailable",
            "price": "json-ld" if price is not None else "unavailable",
            "description": "json-ld" if description else "unavailable",
            "brand": "json-ld" if brand else "unavailable",
            "category": "json-ld" if category_hint else "unavailable",
            "variants": "skuInfos/moduleData" if variants else "unavailable",
        },
        "timings_ms": {"fetch_extract": round((time.perf_counter() - t0) * 1000, 1)},
        "warnings": [],
    }
    if not title:
        payload["warnings"].append("Title not found on public page")
    if not images:
        payload["warnings"].append("No product images extracted")
    if not payload["variants"]:
        payload["warnings"].append(
            "Public page did not expose reliable variants — draft uses a single SKU"
        )
    _cache[clean] = (now, payload)
    return payload


def build_public_clone_draft(
    workspace_id: str,
    *,
    url: str,
    destination_store_id: str,
) -> dict[str, Any]:
    """Public URL → clone draft. May upgrade to connected fetch if ownership proven."""
    repo = get_repo()
    dest = repo.get_store(workspace_id, destination_store_id) or repo.get_store_by_uuid(
        workspace_id, destination_store_id
    )
    if not dest:
        raise PublicDarazError("Destination store not found", code="dest_not_found")

    extracted = fetch_public_product(url)
    item_id = extracted.get("item_id")

    # Smart connected resolution: only if item exists in a connected store warehouse
    if item_id:
        for store in repo.list_stores(workspace_id):
            owned = repo.get_daraz_product_by_item_id(
                workspace_id, str(store["id"]), str(item_id)
            )
            if owned:
                from src.product_fetch import clone_draft_from_connected

                result = clone_draft_from_connected(
                    workspace_id,
                    source_store_id=str(store.get("store_id") or store["id"]),
                    daraz_item_id=str(item_id),
                    destination_store_id=destination_store_id,
                )
                draft = result.get("draft") or {}
                draft["source_type"] = "connected_via_public_url"
                draft["source_url"] = extracted.get("source_url")
                result["draft"] = draft
                result["source_resolution"] = "connected_via_public_url"
                result["public_url"] = extracted.get("source_url")
                warnings = list(result.get("warnings") or [])
                warnings.insert(
                    0,
                    "Item found in a connected store — used seller API path via public URL",
                )
                result["warnings"] = warnings
                return result

    defaults = repo.get_product_defaults(workspace_id)
    prefix = defaults.get("sku_prefix") or DEFAULT_SKU_PREFIX
    initial_qty = int(defaults.get("default_initial_quantity") or 1)
    if initial_qty < 0:
        initial_qty = 0

    warnings = list(extracted.get("warnings") or [])
    errors: list[str] = []
    pkg = resolve_variant_package(
        source_type="daraz_url",
        variant={},
        defaults=defaults,
    )
    warnings.extend(pkg["warnings"])
    errors.extend(pkg["errors"])

    title = extracted.get("title") or "PRODUCT"
    images = list(extracted.get("images") or [])
    enhancement = enhance_description_with_images(
        extracted.get("description_html") or "", images
    )

    existing_skus = list(
        repo.list_destination_seller_skus(workspace_id, str(dest["id"]))
    )
    allocated = list(existing_skus)
    src_variants = extracted.get("variants") or []
    if not isinstance(src_variants, list) or not src_variants:
        src_variants = [
            {
                "price": extracted.get("price"),
                "sale_props": {},
                "seller_sku": None,
            }
        ]

    draft_variants: list[dict[str, Any]] = []
    for idx, raw_v in enumerate(src_variants):
        if not isinstance(raw_v, dict):
            continue
        sale_props = raw_v.get("sale_props") or {}
        if not isinstance(sale_props, dict):
            sale_props = {}
        sku = generate_seller_sku(
            title=title,
            sale_props=sale_props,
            prefix=prefix,
            existing=allocated,
            index=idx,
        )
        allocated.append(sku)
        src_seller = raw_v.get("seller_sku")
        if src_seller:
            warnings.append(
                f"Public SellerSku {src_seller!r} not copied; generated {sku}"
            )
        draft_variants.append(
            {
                "source_variant_id": None,
                "source_seller_sku": src_seller,
                "source_daraz_sku_id": raw_v.get("daraz_sku_id"),
                "sale_props": sale_props,
                "price": raw_v.get("price")
                if raw_v.get("price") is not None
                else extracted.get("price"),
                "quantity": initial_qty,
                "seller_sku": sku,
                "package_weight": pkg["values"].get("package_weight"),
                "package_length": pkg["values"].get("package_length"),
                "package_width": pkg["values"].get("package_width"),
                "package_height": pkg["values"].get("package_height"),
                "package_sources": {
                    f: pkg["values"].get(f"{f}_source")
                    for f in (
                        "package_weight",
                        "package_length",
                        "package_width",
                        "package_height",
                    )
                },
                "images": [{"url": u, "kind": "product"} for u in images[:8]],
            }
        )
    if not draft_variants:
        errors.append("No variants available for draft")

    image_strategy = {
        "strategy": "reuse_cdn"
        if images and all(is_daraz_product_cdn_url(u) for u in images)
        else "mixed_or_migrate",
        "resolved_images": images,
    }

    brand = extracted.get("brand")
    brand_resolution = {
        "status": "PENDING" if brand else "NO_BRAND",
        "brand": brand or "No Brand",
        "message": "Public brand text — resolve against destination category before create",
    }

    duplicates = repo.find_possible_product_duplicates(
        workspace_id,
        str(dest["id"]),
        title=title,
        category_id=None,
    )
    if duplicates:
        warnings.append(f"Possible duplicate(s) on destination: {len(duplicates)}")

    can_create = (
        len(errors) == 0
        and bool(images)
        and bool(title)
        and bool(draft_variants)
        and not duplicates
    )

    draft = {
        "source_type": "public_daraz_url",
        "source_url": extracted.get("source_url"),
        "source_item_id": item_id,
        "destination_store_id": str(dest.get("store_id")),
        "destination_store_uuid": str(dest["id"]),
        "destination_store_name": dest.get("display_name")
        or dest.get("store_name")
        or dest.get("store_id"),
        "product": {
            "title": title,
            "title_en": title,
            "primary_category_id": None,
            "primary_category_name": extracted.get("category_hint"),
            "brand": brand_resolution.get("brand"),
            "attributes": {"brand": brand_resolution.get("brand")},
            "variation": {},
            "description_html": enhancement["html"],
            "short_description_html": None,
            "package_content": None,
            "warranty_type": None,
        },
        "media": {
            "product_images": images,
            "resolved_images": images,
            "image_strategy": image_strategy,
            "description_enhancement": {
                "status": enhancement["enhancement"],
                "message": enhancement["message"],
                "appended_images": enhancement.get("appended_images") or [],
            },
            "video": {"status": "unsupported"},
        },
        "variants": draft_variants,
        "brand_resolution": brand_resolution,
        "possible_duplicates": [
            {
                "id": d.get("id"),
                "title": d.get("title"),
                "daraz_item_id": d.get("daraz_item_id"),
                "match_reason": d.get("match_reason"),
            }
            for d in duplicates[:8]
        ],
        "validation": {
            "missing_mandatory": list(errors),
            "warnings": list(dict.fromkeys(warnings)),
            "errors": list(dict.fromkeys(errors)),
            "can_create": False,  # Create remains gated; also category unresolved
            "create_gated": True,
            "preview_ready": can_create,
            "category": {
                "valid": False,
                "missing_required": ["PrimaryCategory unresolved from public page"],
            },
        },
        "fidelity": {
            "copied": ["Title", "Images", "Description", "Price (starting)"],
            "changed_by_multistore": [
                "SellerSku → MTF-…",
                f"Quantity → workspace initial ({initial_qty})",
                "Package weight/dimensions → workspace defaults",
            ],
            "not_available": [
                "Exact destination category (requires mapping)",
                "Seller-only attributes",
                "Video",
                "Reliable public variants (unless extracted)",
            ],
        },
        "extraction_provenance": extracted.get("provenance"),
    }

    return {
        "draft": draft,
        "fidelity": draft["fidelity"],
        "warnings": draft["validation"]["warnings"],
        "errors": draft["validation"]["errors"],
        "possible_duplicates": draft["possible_duplicates"],
        "source_resolution": "public_daraz_url",
        "timings_ms": extracted.get("timings_ms") or {},
        "create_probe_enabled": False,
    }


def clear_public_cache_for_tests() -> None:
    _cache.clear()
