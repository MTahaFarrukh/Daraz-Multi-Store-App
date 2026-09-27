"""SSRF-safe public Daraz.pk product page extraction → ProductCloneDraft."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import socket
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx

from src.db.repo import get_repo
from src.description_enhance import enhance_description_with_images
from src.image_migrate import is_daraz_product_cdn_url
from src.package_resolve import resolve_variant_package
from src.product_fidelity import parse_money
from src.seller_sku import DEFAULT_SKU_PREFIX, generate_seller_sku

logger = logging.getLogger(__name__)

ALLOWED_HOST_SUFFIXES = (".daraz.pk",)
ALLOWED_HOSTS = frozenset({"daraz.pk", "www.daraz.pk", "acs-m.daraz.pk", "my.daraz.pk"})
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 15.0
CACHE_TTL_S = 30 * 60
MAX_REDIRECTS = 5
PDP_DETAIL_API = "mtop.global.detail.web.getDetailInfo"
PDP_DETAIL_APP_KEY = "24677475"
PDP_DETAIL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_ITEM_RE = re.compile(r"(?:-i|/products/i)(\d{6,})", re.I)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
# Short-lived cache for getDetailInfo module payloads (item_id → fields).
_detail_price_cache: dict[str, tuple[float, dict[str, Any]]] = {}
DETAIL_CACHE_TTL_S = 30 * 60


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
    from src.product_fidelity import parse_money

    offers = product_ld.get("offers")
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if not isinstance(offers, dict):
        return None
    return parse_money(offers.get("price") or offers.get("lowPrice"))


def _balanced_json(html: str, start: int, *, limit: int = 800_000) -> Any | None:
    if start < 0 or start >= len(html):
        return None
    opener = html[start]
    if opener not in "{[":
        return None
    closer = "}" if opener == "{" else "]"
    depth = 0
    end = None
    for i, ch in enumerate(html[start : start + limit], start=start):
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None
    try:
        return json.loads(html[start:end])
    except json.JSONDecodeError:
        return None


def _find_json_after(html: str, pattern: str) -> Any | None:
    m = re.search(pattern, html or "")
    if not m:
        return None
    return _balanced_json(html, m.start(1))


def _sku_price_fields(sku: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    """Return (regular, special, source) from a skuInfos-like object.

    Seller special_price is only accepted from explicit seller fields — never from
    voucher/campaign display prices.
    """
    from src.product_fidelity import parse_money

    regular = None
    special = None
    source = None

    raw_price = sku.get("price")
    if isinstance(raw_price, dict):
        regular = parse_money(
            raw_price.get("originalPrice")
            or raw_price.get("price")
            or raw_price.get("salePrice")
        )
        special = parse_money(
            raw_price.get("specialPrice")
            or raw_price.get("special_price")
            or raw_price.get("salePrice")
        )
        # If only one numeric present under price.price, treat as regular.
        if regular is None:
            regular = parse_money(raw_price.get("price"))
        if (
            special is not None
            and regular is not None
            and special >= regular
        ):
            special = None
        source = "skuInfos.price"
    else:
        regular = parse_money(raw_price)

    if special is None:
        special = parse_money(sku.get("special_price") or sku.get("specialPrice"))
        if special is not None:
            source = source or "skuInfos.special_price"

    if regular is not None and source is None:
        source = "skuInfos"

    return regular, special, source


def _prop_path_to_sale_props(
    prop_path: str | None, properties: list[dict[str, Any]] | None
) -> dict[str, Any]:
    if not prop_path or not isinstance(properties, list):
        return {"propPath": prop_path} if prop_path else {}
    pid_to_name: dict[str, str] = {}
    vid_to_name: dict[str, dict[str, str]] = {}
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        pid = str(prop.get("pid") or "")
        pname = str(prop.get("name") or pid)
        if pid:
            pid_to_name[pid] = pname
            vid_to_name[pid] = {}
            for val in prop.get("values") or []:
                if not isinstance(val, dict):
                    continue
                vid = str(val.get("vid") if val.get("vid") is not None else "")
                vname = str(val.get("name") or vid)
                if vid:
                    vid_to_name[pid][vid] = vname
    sale: dict[str, Any] = {}
    for part in str(prop_path).split(";"):
        if ":" not in part:
            continue
        pid, vid = part.split(":", 1)
        name = pid_to_name.get(pid) or pid
        label = vid_to_name.get(pid, {}).get(vid) or vid
        sale[name] = label
    return sale or ({"propPath": prop_path} if prop_path else {})


def _extract_variants_structured(html: str) -> list[dict[str, Any]]:
    """Extract per-SKU identity + prices from skuBase + skuInfos (best effort)."""
    sku_base = _find_json_after(html, r'"skuBase"\s*:\s*(\{)')
    sku_infos = _find_json_after(html, r'"skuInfos"\s*:\s*(\{)')
    properties = None
    base_skus: list[dict[str, Any]] = []
    if isinstance(sku_base, dict):
        properties = sku_base.get("properties")
        raw_skus = sku_base.get("skus")
        if isinstance(raw_skus, list):
            base_skus = [s for s in raw_skus if isinstance(s, dict)]

    infos: dict[str, dict[str, Any]] = {}
    if isinstance(sku_infos, dict):
        for key, sku in sku_infos.items():
            if not isinstance(sku, dict):
                continue
            if str(key) == "0" and not sku.get("skuId") and len(sku_infos) > 1:
                continue
            sid = str(sku.get("skuId") or key)
            infos[sid] = sku

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(
        *,
        sku_id: str,
        sale_props: dict[str, Any],
        info: dict[str, Any] | None,
        prop_path: str | None = None,
    ) -> None:
        if sku_id in seen:
            return
        seen.add(sku_id)
        info = info or {}
        regular, special, price_source = _sku_price_fields(info)
        qty = None
        q = info.get("quantity")
        if isinstance(q, dict):
            lim = q.get("limit") if isinstance(q.get("limit"), dict) else {}
            qty = lim.get("max")
        out.append(
            {
                "daraz_sku_id": sku_id,
                "seller_sku": info.get("sellerSku") or info.get("SellerSku"),
                "sale_props": sale_props,
                "prop_path": prop_path,
                "price": regular,
                "special_price": special,
                "special_from_time": info.get("special_from_time")
                or info.get("specialFromTime"),
                "special_to_time": info.get("special_to_time")
                or info.get("specialToTime"),
                "price_confidence": (
                    "high"
                    if regular is not None and price_source
                    else "missing"
                ),
                "price_source": price_source,
                "category_id": info.get("categoryId") or info.get("category_id"),
                "image": info.get("image"),
                "quantity_hint": qty,
            }
        )

    if base_skus:
        for sku in base_skus:
            sku_id = str(sku.get("skuId") or sku.get("cartSkuId") or "")
            if not sku_id:
                continue
            prop_path = sku.get("propPath")
            sale = _prop_path_to_sale_props(
                str(prop_path) if prop_path else None,
                properties if isinstance(properties, list) else None,
            )
            _append(
                sku_id=sku_id,
                sale_props=sale,
                info=infos.get(sku_id),
                prop_path=str(prop_path) if prop_path else None,
            )
    else:
        for sku_id, info in infos.items():
            sale: dict[str, Any] = {}
            props = info.get("saleProp") or info.get("properties")
            if isinstance(props, dict):
                sale = {str(k): v for k, v in props.items()}
            elif isinstance(info.get("propPath"), str) and info.get("propPath"):
                sale = _prop_path_to_sale_props(info.get("propPath"), None)
            _append(sku_id=sku_id, sale_props=sale, info=info)

    return out


def _extract_pdt_price(html: str) -> float | None:
    from src.product_fidelity import parse_money

    for pat in (
        r'"pdt_price"\s*:\s*"([^"]+)"',
        r'\\"pdt_price\\"\s*:\s*\\"([^\\"]+)\\"',
    ):
        m = re.search(pat, html or "")
        if m:
            return parse_money(m.group(1))
    return None


def _extract_reg_category_id(html: str) -> str | None:
    m = re.search(r'"regCategoryId"\s*:\s*"(\d+)"', html or "")
    if m:
        return m.group(1)
    m = re.search(r'\\"regCategoryId\\"\s*:\s*\\"(\d+)\\"', html or "")
    return m.group(1) if m else None


def _fetch_catalog_enrichment(item_id: str, *, host: str = "www.daraz.pk") -> dict[str, Any]:
    """Cheap catalog ajax enrichment (category path, brand, listing prices).

    Listing ``price`` may include campaigns/vouchers — never treat as seller
    Special Price. ``originalPrice`` is used only as a regular-price hint when
    per-SKU structured prices are missing.
    """
    from src.product_fidelity import parse_money

    t0 = time.perf_counter()
    out: dict[str, Any] = {"timings_ms": {}}
    if not item_id or not _host_allowed(host):
        return out
    try:
        _resolve_public(host)
        url = f"https://{host}/catalog/?_keyori=ss&from=input&q={item_id}&ajax=true"
        with httpx.Client(
            timeout=min(TIMEOUT_S, 8.0),
            follow_redirects=True,
            headers={
                "User-Agent": "MultiStoreProductImport/1.0",
                "Accept": "application/json",
            },
        ) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            out["timings_ms"]["catalog"] = round((time.perf_counter() - t0) * 1000, 1)
            return out
        data = resp.json()
        mods = data.get("mods") if isinstance(data, dict) else None
        items = mods.get("listItems") if isinstance(mods, dict) else None
        if not isinstance(items, list) or not items:
            out["timings_ms"]["catalog"] = round((time.perf_counter() - t0) * 1000, 1)
            return out
        hit = None
        for row in items:
            if not isinstance(row, dict):
                continue
            if str(row.get("itemId") or row.get("nid") or "") == str(item_id):
                hit = row
                break
        if hit is None and isinstance(items[0], dict):
            hit = items[0]
        if not isinstance(hit, dict):
            out["timings_ms"]["catalog"] = round((time.perf_counter() - t0) * 1000, 1)
            return out
        cats = hit.get("categories")
        category_ids = [str(c) for c in cats] if isinstance(cats, list) else []
        out.update(
            {
                "brand": hit.get("brandName"),
                "brand_id": hit.get("brandId"),
                "category_ids": category_ids,
                "category_id": category_ids[-1] if category_ids else None,
                "original_price": parse_money(hit.get("originalPrice")),
                "display_price": parse_money(hit.get("price")),
                "cheapest_sku_id": (
                    str(hit.get("skuId")) if hit.get("skuId") is not None else None
                ),
                "title": hit.get("name"),
                # Explicitly NOT seller special — catalog price is often promo.
                "display_price_is_not_special": True,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("catalog enrichment skipped: %s", exc)
    out["timings_ms"]["catalog"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def _apply_price_fallbacks(
    variants: list[dict[str, Any]],
    *,
    pdt_price: float | None,
    catalog: dict[str, Any],
    json_ld_price: float | None,
) -> list[dict[str, Any]]:
    """Fill missing regular prices carefully — never invent special prices."""
    if not variants:
        return variants

    known = [v for v in variants if v.get("price") is not None]
    if len(known) == len(variants):
        return variants

    # Prefer catalog originalPrice / pdt_price / json-ld as product-level regular.
    product_regular = (
        catalog.get("original_price")
        if catalog.get("original_price") is not None
        else pdt_price
        if pdt_price is not None
        else json_ld_price
    )

    if product_regular is None:
        return variants

    # Single SKU — safe to apply product-level price.
    if len(variants) == 1:
        if variants[0].get("price") is None:
            variants[0]["price"] = product_regular
            variants[0]["price_confidence"] = "medium"
            variants[0]["price_source"] = (
                "catalog.originalPrice"
                if catalog.get("original_price") is not None
                else "pdt_price"
                if pdt_price is not None
                else "json-ld"
            )
        return variants

    # Multi-SKU: only apply product-level price when EVERY variant is missing
    # AND we have no evidence they differ — still mark medium confidence and
    # leave special untouched. Callers must NOT treat this as verified per-SKU
    # fidelity when prices were absent from skuInfos.
    if not known:
        cheapest = catalog.get("cheapest_sku_id")
        for v in variants:
            # Prefer attaching listing original to cheapest SKU only when ids match;
            # otherwise leave unresolved so UI can collect per-variant prices.
            if cheapest and str(v.get("daraz_sku_id")) == str(cheapest):
                v["price"] = product_regular
                v["price_confidence"] = "medium"
                v["price_source"] = "catalog.originalPrice"
            # Do not flatten product_regular onto every SKU.
        # If still all missing (no cheapest match), attach to none — Needs Attention.
        still_missing = [v for v in variants if v.get("price") is None]
        if len(still_missing) == len(variants) and len(variants) > 1:
            # Keep unresolved — better than flattening.
            pass
        return variants

    return variants


def _mtop_sign(token: str, t: str, data: str) -> str:
    return hashlib.md5(
        f"{token}&{t}&{PDP_DETAIL_APP_KEY}&{data}".encode("utf-8")
    ).hexdigest()


def _cookie_h5_token(client: httpx.Client) -> str:
    val = client.cookies.get("_m_h5_tk") or ""
    return val.split("_", 1)[0] if "_" in val else ""


def _normalize_detail_price_obj(price_obj: Any) -> dict[str, Any]:
    """Map getDetailInfo skuInfos[].price → regular/current semantics.

    Live PDP returns::

        price.originalPrice.value  (list / struck-through)
        price.salePrice.value      (current displayed)

    ``salePrice`` may include campaigns/vouchers — do NOT treat it as seller
    Special Price. Prefer ``originalPrice`` as regular when present; otherwise
    fall back to ``salePrice`` as the SKU's best-known regular price.
    """
    out: dict[str, Any] = {
        "regular_price": None,
        "current_price": None,
        "original_price": None,
        "special_price": None,  # intentionally unused for public detail
        "price_source": "mtop.getDetailInfo",
        "price_confidence": "missing",
    }
    if not isinstance(price_obj, dict):
        return out

    original = None
    sale = None
    op = price_obj.get("originalPrice")
    if isinstance(op, dict):
        original = parse_money(op.get("value") if op.get("value") is not None else op.get("text"))
    else:
        original = parse_money(op)
    sp = price_obj.get("salePrice")
    if isinstance(sp, dict):
        sale = parse_money(sp.get("value") if sp.get("value") is not None else sp.get("text"))
    else:
        sale = parse_money(sp)
    # Some payloads use flat price/priceText
    if original is None and sale is None:
        original = parse_money(price_obj.get("price") or price_obj.get("value"))

    out["original_price"] = original
    out["current_price"] = sale
    if original is not None:
        out["regular_price"] = original
        out["price_confidence"] = "high"
    elif sale is not None:
        out["regular_price"] = sale
        out["price_confidence"] = "medium"
        out["price_source"] = "mtop.getDetailInfo.salePrice"
    return out


def sku_prices_from_detail_fields(fields: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract sku_id → price info from getDetailInfo module fields."""
    sku_infos = fields.get("skuInfos")
    if not isinstance(sku_infos, dict):
        root = fields.get("root")
        if isinstance(root, dict):
            inner = root.get("fields") if isinstance(root.get("fields"), dict) else root
            sku_infos = inner.get("skuInfos") if isinstance(inner, dict) else None
    if not isinstance(sku_infos, dict):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for key, sku in sku_infos.items():
        if not isinstance(sku, dict):
            continue
        if str(key) == "0" and not sku.get("skuId") and len(sku_infos) > 1:
            continue
        sku_id = str(sku.get("skuId") or key)
        if not sku_id or sku_id == "0":
            # Skip aggregate default slot when real SKUs exist
            if any(str(k) != "0" for k in sku_infos.keys()):
                continue
        priced = _normalize_detail_price_obj(sku.get("price"))
        if priced.get("regular_price") is None:
            continue
        out[sku_id] = {
            **priced,
            "category_id": sku.get("categoryId") or sku.get("category_id"),
            "image": sku.get("image"),
            "seller_id": sku.get("sellerId"),
        }
    return out


def merge_detail_prices_into_variants(
    variants: list[dict[str, Any]],
    price_by_sku: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge getDetailInfo prices onto variants by Daraz SKU ID (never by index)."""
    if not variants or not price_by_sku:
        return variants
    for v in variants:
        if not isinstance(v, dict):
            continue
        if v.get("price") is not None:
            continue
        sku_id = str(v.get("daraz_sku_id") or v.get("skuId") or "")
        hit = price_by_sku.get(sku_id)
        if not hit:
            continue
        v["price"] = hit.get("regular_price")
        v["price_confidence"] = hit.get("price_confidence") or "high"
        v["price_source"] = hit.get("price_source") or "mtop.getDetailInfo"
        v["current_price"] = hit.get("current_price")
        v["original_price"] = hit.get("original_price")
        # Do NOT copy salePrice into special_price (campaign contamination).
        if not v.get("category_id") and hit.get("category_id"):
            v["category_id"] = hit["category_id"]
        if not v.get("image") and hit.get("image"):
            v["image"] = hit["image"]
    return variants


def _fetch_pdp_detail_sku_prices(
    product_url: str,
    *,
    item_id: str | None = None,
) -> dict[str, Any]:
    """Fetch hydrated PDP module via mtop.global.detail.web.getDetailInfo.

    This is the same API the live Daraz PDP uses after SSR to populate
    ``skuInfos[].price`` (originalPrice / salePrice) for every SKU in one call.
    """
    t0 = time.perf_counter()
    result: dict[str, Any] = {
        "prices_by_sku": {},
        "ok": False,
        "error": None,
        "source": PDP_DETAIL_API,
        "timings_ms": {},
    }
    cache_key = str(item_id or product_url)
    now = time.time()
    if cache_key in _detail_price_cache:
        ts, cached = _detail_price_cache[cache_key]
        if now - ts < DETAIL_CACHE_TTL_S:
            result.update(cached)
            result["cache_hit"] = True
            result["timings_ms"] = {
                "price_resolution_ms": round((time.perf_counter() - t0) * 1000, 1),
                "fallback_fetch_ms": 0.0,
            }
            return result

    host = "acs-m.daraz.pk"
    try:
        _resolve_public(host)
    except PublicDarazError as exc:
        result["error"] = str(exc)
        result["timings_ms"]["price_resolution_ms"] = round(
            (time.perf_counter() - t0) * 1000, 1
        )
        return result

    parsed = urlparse(product_url)
    path = parsed.path or "/"
    uri = path

    headers = {
        "User-Agent": PDP_DETAIL_UA,
        "Referer": product_url,
        "Accept": "application/json",
    }

    try:
        with httpx.Client(
            timeout=min(TIMEOUT_S, 12.0),
            follow_redirects=True,
            headers=headers,
        ) as client:
            # Warm cookies / token (same flow as PDP JS).
            warm_data = json.dumps(
                {
                    "deviceType": "pc",
                    "path": product_url,
                    "uri": uri,
                    "headerParams": json.dumps(
                        {"user-agent": PDP_DETAIL_UA}, separators=(",", ":")
                    ),
                    "cookieParams": "{}",
                    "requestParams": "{}",
                },
                separators=(",", ":"),
            )
            client.get(
                f"https://{host}/h5/{PDP_DETAIL_API}/1.0/"
                f"?jsv=2.7.0&appKey={PDP_DETAIL_APP_KEY}&api={PDP_DETAIL_API}&v=1.0"
                f"&type=originaljson&dataType=json&data={quote(warm_data)}",
            )
            token = _cookie_h5_token(client)
            if not token:
                result["error"] = "mtop_token_missing"
                result["timings_ms"]["price_resolution_ms"] = round(
                    (time.perf_counter() - t0) * 1000, 1
                )
                return result

            cookie_params = {c.name: c.value for c in client.cookies.jar}
            data_obj = {
                "deviceType": "pc",
                "path": product_url,
                "uri": uri,
                "headerParams": json.dumps(
                    {"user-agent": PDP_DETAIL_UA}, separators=(",", ":")
                ),
                "cookieParams": json.dumps(cookie_params, separators=(",", ":")),
                "requestParams": "{}",
            }
            data = json.dumps(data_obj, separators=(",", ":"))
            t_ms = str(int(time.time() * 1000))
            sign = _mtop_sign(token, t_ms, data)
            t_fetch = time.perf_counter()
            resp = client.post(
                f"https://{host}/h5/{PDP_DETAIL_API}/1.0/",
                params={
                    "jsv": "2.7.0",
                    "appKey": PDP_DETAIL_APP_KEY,
                    "t": t_ms,
                    "sign": sign,
                    "api": PDP_DETAIL_API,
                    "v": "1.0",
                    "type": "originaljson",
                    "dataType": "json",
                },
                data={"data": data},
                headers={
                    **headers,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            fallback_ms = round((time.perf_counter() - t_fetch) * 1000, 1)
            body = resp.json()
            ret = " ".join(body.get("ret") or [])
            # One token-retry (matches PDP JS behaviour).
            if "TOKEN" in ret.upper() or "ILLEGAL_ACCESS" in ret.upper():
                token = _cookie_h5_token(client) or token
                cookie_params = {c.name: c.value for c in client.cookies.jar}
                data_obj["cookieParams"] = json.dumps(
                    cookie_params, separators=(",", ":")
                )
                data = json.dumps(data_obj, separators=(",", ":"))
                t_ms = str(int(time.time() * 1000))
                sign = _mtop_sign(token, t_ms, data)
                t_fetch = time.perf_counter()
                resp = client.post(
                    f"https://{host}/h5/{PDP_DETAIL_API}/1.0/",
                    params={
                        "jsv": "2.7.0",
                        "appKey": PDP_DETAIL_APP_KEY,
                        "t": t_ms,
                        "sign": sign,
                        "api": PDP_DETAIL_API,
                        "v": "1.0",
                        "type": "originaljson",
                        "dataType": "json",
                    },
                    data={"data": data},
                    headers={
                        **headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                fallback_ms += round((time.perf_counter() - t_fetch) * 1000, 1)
                body = resp.json()
                ret = " ".join(body.get("ret") or [])

            if "SUCCESS" not in ret.upper():
                result["error"] = ret or f"http_{resp.status_code}"
                result["timings_ms"] = {
                    "price_resolution_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "fallback_fetch_ms": fallback_ms,
                }
                return result

            module = (body.get("data") or {}).get("module")
            if isinstance(module, str):
                fields = json.loads(module)
            elif isinstance(module, dict):
                fields = module
            else:
                result["error"] = "module_missing"
                result["timings_ms"] = {
                    "price_resolution_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "fallback_fetch_ms": fallback_ms,
                }
                return result

            prices = sku_prices_from_detail_fields(fields)
            result["prices_by_sku"] = prices
            result["ok"] = bool(prices)
            result["sku_count"] = len(prices)
            result["timings_ms"] = {
                "price_resolution_ms": round((time.perf_counter() - t0) * 1000, 1),
                "fallback_fetch_ms": fallback_ms,
            }
            _detail_price_cache[cache_key] = (
                now,
                {
                    "prices_by_sku": prices,
                    "ok": result["ok"],
                    "error": None,
                    "source": PDP_DETAIL_API,
                    "sku_count": len(prices),
                },
            )
            return result
    except Exception as exc:  # noqa: BLE001
        logger.info("getDetailInfo price resolution failed: %s", exc)
        result["error"] = str(exc)
        result["timings_ms"]["price_resolution_ms"] = round(
            (time.perf_counter() - t0) * 1000, 1
        )
        return result


def _extract_sku_infos_best_effort(html: str) -> list[dict[str, Any]]:
    """Backward-compatible alias used by tests / callers."""
    return _extract_variants_structured(html)


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
    ssr_fetch_ms = round((time.perf_counter() - t0) * 1000, 1)

    t_parse = time.perf_counter()
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

    variants = _extract_variants_structured(html)
    pdt_price = _extract_pdt_price(html)
    reg_cat = _extract_reg_category_id(html)
    parse_ms = round((time.perf_counter() - t_parse) * 1000, 1)

    host = urlparse(clean).hostname or "www.daraz.pk"
    catalog = _fetch_catalog_enrichment(str(item_id or ""), host=host)

    # Phase 4D.5.1: hydrate missing SSR prices via getDetailInfo (all SKUs, one call)
    # BEFORE catalog cheapest hints — catalog must never flatten across variants.
    detail_meta: dict[str, Any] = {"ok": False, "skipped": True}
    missing_before_detail = sum(1 for v in variants if v.get("price") is None)
    if missing_before_detail and variants:
        detail_meta = _fetch_pdp_detail_sku_prices(clean, item_id=str(item_id or ""))
        detail_meta["skipped"] = False
        if detail_meta.get("ok"):
            variants = merge_detail_prices_into_variants(
                variants, detail_meta.get("prices_by_sku") or {}
            )

    variants = _apply_price_fallbacks(
        variants,
        pdt_price=pdt_price,
        catalog=catalog,
        json_ld_price=price,
    )

    # Category: prefer leaf from catalog categories, else skuInfos categoryId.
    category_id = catalog.get("category_id")
    category_confidence = "high" if category_id else None
    category_source = "catalog.categories" if category_id else None
    if not category_id:
        for v in variants:
            if v.get("category_id"):
                category_id = str(v["category_id"])
                category_confidence = "high"
                category_source = "skuInfos.categoryId"
                break
    if not category_id and reg_cat:
        category_id = str(reg_cat)
        category_confidence = "medium"
        category_source = "regCategoryId"

    if not brand and catalog.get("brand"):
        brand = catalog.get("brand")
    if not title and catalog.get("title"):
        title = catalog.get("title")

    # Product-level price hint (never used to flatten multi-SKU silently).
    product_price = None
    priced = [v.get("price") for v in variants if v.get("price") is not None]
    if len(priced) == 1 and len(variants) == 1:
        product_price = priced[0]
    elif len(set(round(float(p), 4) for p in priced)) == 1 and priced:
        product_price = priced[0]
    else:
        product_price = (
            catalog.get("original_price")
            if catalog.get("original_price") is not None
            else pdt_price
            if pdt_price is not None
            else price
        )

    missing_variant_prices = sum(1 for v in variants if v.get("price") is None)
    special_detected = sum(
        1 for v in variants if v.get("special_price") is not None
    )

    price_source_label = "unavailable"
    if any(
        str(v.get("price_source") or "").startswith("mtop.getDetailInfo")
        for v in variants
    ):
        price_source_label = "mtop.getDetailInfo"
    elif any(v.get("price_source") == "skuInfos" or str(v.get("price_source") or "").startswith("skuInfos") for v in variants):
        price_source_label = "skuInfos"
    elif product_price is not None:
        price_source_label = "catalog/pdt"

    timings = {
        "ssr_fetch_ms": ssr_fetch_ms,
        "structured_parse_ms": parse_ms,
        "public_fetch_ms": round((time.perf_counter() - t0) * 1000, 1),
        "parse_ms": parse_ms,
        "fetch_extract": round((time.perf_counter() - t0) * 1000, 1),
        "total_source_extract_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    timings.update(catalog.get("timings_ms") or {})
    timings.update(detail_meta.get("timings_ms") or {})

    payload = {
        "source_type": "public_daraz_url",
        "source_url": clean,
        "item_id": item_id,
        "title": title,
        "brand": brand,
        "description_html": description,
        "images": images,
        "price": product_price,
        "category_hint": category_hint,
        "category_id": category_id,
        "category_ids": catalog.get("category_ids") or [],
        "category_resolution": {
            "category_id": category_id,
            "category_name": category_hint,
            "source": category_source,
            "confidence": category_confidence,
        },
        "variants": variants,
        "pricing_summary": {
            "variant_count": len(variants),
            "regular_prices_complete": missing_variant_prices == 0 and bool(variants),
            "missing_variant_prices": missing_variant_prices,
            "special_prices_detected": special_detected,
            "pdt_price": pdt_price,
            "catalog_original_price": catalog.get("original_price"),
            "catalog_display_price_ignored_as_special": catalog.get("display_price"),
            "price_resolution_source": price_source_label,
            "detail_api": detail_meta.get("source") if not detail_meta.get("skipped") else None,
            "detail_ok": bool(detail_meta.get("ok")),
            "detail_error": detail_meta.get("error"),
            "detail_sku_count": detail_meta.get("sku_count"),
        },
        "provenance": {
            "title": "json-ld" if ld.get("name") else ("catalog" if title else "unavailable"),
            "images": "json-ld" if images else "unavailable",
            "price": price_source_label,
            "description": "json-ld" if description else "unavailable",
            "brand": "json-ld" if ld.get("brand") else ("catalog" if brand else "unavailable"),
            "category": category_source or ("json-ld" if category_hint else "unavailable"),
            "variants": "skuBase+skuInfos" if variants else "unavailable",
        },
        "timings_ms": timings,
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
    if missing_variant_prices:
        payload["warnings"].append(
            f"{missing_variant_prices} variant(s) still missing regular price after "
            "SSR + getDetailInfo resolution"
        )
    if catalog.get("display_price") is not None:
        payload["warnings"].append(
            "Catalog display price ignored for Special Price (may include campaigns/vouchers)"
        )
    if detail_meta.get("ok"):
        payload["warnings"].append(
            "Variant prices hydrated via mtop.global.detail.web.getDetailInfo "
            "(salePrice not treated as seller Special Price)"
        )
    _cache[clean] = (now, payload)
    return payload


def find_connected_owner_for_item(
    workspace_id: str, item_id: str | None
) -> dict[str, Any] | None:
    """Return a connected store that already owns ``item_id``, if any."""
    if not item_id:
        return None
    repo = get_repo()
    for store in repo.list_stores(workspace_id):
        owned = repo.get_daraz_product_by_item_id(
            workspace_id, str(store["id"]), str(item_id)
        )
        if owned:
            return store
    return None


def build_public_clone_draft_from_extracted(
    workspace_id: str,
    *,
    extracted: dict[str, Any],
    destination_store_id: str,
) -> dict[str, Any]:
    """Build a public-URL clone draft from an already-fetched extract (no HTTP)."""
    repo = get_repo()
    dest = repo.get_store(workspace_id, destination_store_id) or repo.get_store_by_uuid(
        workspace_id, destination_store_id
    )
    if not dest:
        raise PublicDarazError("Destination store not found", code="dest_not_found")

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
    item_id = extracted.get("item_id")

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
                else (
                    extracted.get("price") if len(src_variants) == 1 else None
                ),
                "special_price": raw_v.get("special_price"),
                "special_from_time": raw_v.get("special_from_time"),
                "special_to_time": raw_v.get("special_to_time"),
                "price_confidence": raw_v.get("price_confidence")
                or ("medium" if raw_v.get("price") is not None else "missing"),
                "price_source": raw_v.get("price_source"),
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
                "images": (
                    [{"url": raw_v["image"], "kind": "variant"}]
                    if raw_v.get("image")
                    else [{"url": u, "kind": "product"} for u in images[:8]]
                ),
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

    cat_res = extracted.get("category_resolution") or {}
    primary_category_id = None
    cat_confidence = cat_res.get("confidence")
    if extracted.get("category_id") and cat_confidence in {"high", "medium"}:
        try:
            primary_category_id = int(str(extracted["category_id"]))
        except (TypeError, ValueError):
            primary_category_id = None

    brand = extracted.get("brand")
    brand_resolution = {
        "status": "PENDING" if brand else "NO_BRAND",
        "brand": brand or "No Brand",
        "message": (
            "Public brand text — resolve against destination category before create"
            if brand
            else "No public brand — will use destination No Brand when supported"
        ),
        "used_no_brand": not bool(brand),
    }

    duplicates = repo.find_possible_product_duplicates(
        workspace_id,
        str(dest["id"]),
        title=title,
        category_id=primary_category_id,
    )
    if duplicates:
        warnings.append(f"Possible duplicate(s) on destination: {len(duplicates)}")

    unresolved_prices = [
        {
            "key": str(v.get("source_daraz_sku_id") or v.get("seller_sku")),
            "label": " / ".join(
                f"{k}: {val}" for k, val in (v.get("sale_props") or {}).items()
            )
            or "Default",
            "sale_props": v.get("sale_props") or {},
        }
        for v in draft_variants
        if v.get("price") is None
    ]
    if unresolved_prices:
        errors.append("missing_variant_prices")
        warnings.append(
            f"Price required for {len(unresolved_prices)} variant(s) before create"
        )

    can_create = (
        len(errors) == 0
        and bool(images)
        and bool(title)
        and bool(draft_variants)
        and not duplicates
        and primary_category_id is not None
        and not unresolved_prices
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
            "primary_category_id": primary_category_id,
            "primary_category_name": extracted.get("category_hint"),
            "brand": brand_resolution.get("brand"),
            "attributes": {"brand": brand_resolution.get("brand")},
            "variation": {},
            "description_html": enhancement["html"],
            "short_description_html": None,
            "package_content": None,
            "warranty_type": None,
        },
        "category_resolution": {
            "category_id": primary_category_id,
            "category_name": extracted.get("category_hint"),
            "source": cat_res.get("source"),
            "confidence": cat_confidence,
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
                "match_score": d.get("match_score"),
            }
            for d in duplicates[:8]
        ],
        "unresolved_variants": unresolved_prices,
        "pricing_summary": extracted.get("pricing_summary") or {},
        "validation": {
            "missing_mandatory": list(errors),
            "warnings": list(dict.fromkeys(warnings)),
            "errors": list(dict.fromkeys(errors)),
            "can_create": False,
            "create_gated": True,
            "preview_ready": can_create,
            "category": {
                "valid": primary_category_id is not None,
                "missing_required": (
                    []
                    if primary_category_id is not None
                    else ["PrimaryCategory unresolved from public page"]
                ),
            },
        },
        "fidelity": {
            "copied": ["Title", "Images", "Description", "Per-variant prices (when available)"],
            "changed_by_multistore": [
                "SellerSku → MTF-…",
                f"Quantity → workspace initial ({initial_qty})",
                "Package weight/dimensions → workspace defaults",
            ],
            "not_available": [
                *(
                    []
                    if primary_category_id is not None
                    else ["Exact destination category (requires mapping)"]
                ),
                "Seller-only attributes",
                "Video",
                "Campaign/voucher prices (intentionally ignored)",
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


def build_public_clone_draft(
    workspace_id: str,
    *,
    url: str,
    destination_store_id: str,
) -> dict[str, Any]:
    """Public URL → clone draft. May upgrade to connected fetch if ownership proven."""
    extracted = fetch_public_product(url)
    owner = find_connected_owner_for_item(workspace_id, extracted.get("item_id"))
    if owner:
        from src.product_fetch import clone_draft_from_connected

        result = clone_draft_from_connected(
            workspace_id,
            source_store_id=str(owner.get("store_id") or owner["id"]),
            daraz_item_id=str(extracted.get("item_id")),
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

    return build_public_clone_draft_from_extracted(
        workspace_id,
        extracted=extracted,
        destination_store_id=destination_store_id,
    )


def clear_public_cache_for_tests() -> None:
    _cache.clear()
    _detail_price_cache.clear()
