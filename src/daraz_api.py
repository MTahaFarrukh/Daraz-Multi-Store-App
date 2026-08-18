"""
Minimal Daraz Open Platform client for Pakistan (investigation POC).

Official references:
- https://open.daraz.com/
- https://open.alitrip.com/docs/doc.htm?articleId=120322&docType=1&treeId=754 (signing)
- https://open.alitrip.com/docs/doc.htm?articleId=120222&docType=1&treeId=754 (OAuth)

This module does NOT call live APIs unless you provide real credentials in .env
and run the __main__ block intentionally.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_API_BASE = "https://api.daraz.pk/rest"


class DarazApiError(Exception):
    """Raised when Daraz returns a non-success API response."""

    def __init__(self, message: str, *, code: str | None = None, payload: dict | None = None):
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


class DarazClient:
    """Signed REST client for Daraz Open Platform (Pakistan)."""

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        access_token: str | None = None,
        api_base: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.app_key = app_key or os.getenv("DARAZ_APP_KEY", "")
        self.app_secret = app_secret or os.getenv("DARAZ_APP_SECRET", "")
        self.access_token = access_token or os.getenv("DARAZ_ACCESS_TOKEN", "")
        self.api_base = (api_base or os.getenv("DARAZ_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        self.timeout = timeout

        if not self.app_key or not self.app_secret:
            raise ValueError("DARAZ_APP_KEY and DARAZ_APP_SECRET are required")

    # ------------------------------------------------------------------
    # Signing (official algorithm)
    # ------------------------------------------------------------------

    @staticmethod
    def sign(secret: str, api_path: str, parameters: dict[str, Any], body: str | None = None) -> str:
        """
        HMAC-SHA256 signature per Daraz Open Platform docs.

        Concatenate: api_path + sorted(key+value pairs) [+ body for POST], excluding 'sign'.
        Return uppercase hex digest.
        """
        filtered = {
            k: v
            for k, v in parameters.items()
            if k != "sign" and v is not None and v != ""
        }
        ordered = "".join(f"{k}{filtered[k]}" for k in sorted(filtered))
        payload = f"{api_path}{ordered}{body or ''}"
        digest = hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest.upper()

    def _common_params(self) -> dict[str, str]:
        return {
            "app_key": self.app_key,
            "sign_method": "sha256",
            "timestamp": str(int(time.time() * 1000)),
        }

    def _request(
        self,
        api_path: str,
        *,
        method: str = "GET",
        business_params: dict[str, Any] | None = None,
        body: str | None = None,
        require_token: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = self._common_params()
        if require_token:
            if not self.access_token:
                raise ValueError("DARAZ_ACCESS_TOKEN is required for this API call")
            params["access_token"] = self.access_token
        if business_params:
            params.update(business_params)

        params["sign"] = self.sign(self.app_secret, api_path, params, body)

        url = f"{self.api_base}{api_path}"
        with httpx.Client(timeout=self.timeout) as client:
            if method.upper() == "GET":
                response = client.get(url, params=params)
            else:
                response = client.post(
                    url,
                    params=params,
                    content=body,
                    headers={"Content-Type": "application/json"},
                )

        response.raise_for_status()
        data = response.json()
        if str(data.get("code", "0")) != "0":
            raise DarazApiError(
                data.get("message") or data.get("type") or "Daraz API error",
                code=str(data.get("code")),
                payload=data,
            )
        return data

    # ------------------------------------------------------------------
    # Auth (system APIs — no access_token)
    # ------------------------------------------------------------------

    def create_token_from_code(self, code: str) -> dict[str, Any]:
        """Exchange OAuth authorization code for access/refresh tokens."""
        return self._request(
            "/auth/token/create",
            business_params={"code": code},
            require_token=False,
        )

    def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh access token. Save the new refresh_token from the response."""
        return self._request(
            "/auth/token/refresh",
            business_params={"refresh_token": refresh_token},
            require_token=False,
        )

    @staticmethod
    def build_authorize_url(
        app_key: str,
        redirect_uri: str,
        *,
        authorize_base: str = "https://api.daraz.pk/oauth/authorize",
        force_auth: bool = True,
        state: str | None = None,
    ) -> str:
        """
        Build seller OAuth URL. Seller must open this in a browser and sign in.

        Manual step: after authorization, capture ?code= from redirect_uri and call
        create_token_from_code(code).
        """
        params = {
            "response_type": "code",
            "client_id": app_key,
            "redirect_uri": redirect_uri,
        }
        if force_auth:
            params["force_auth"] = "true"
        if state:
            params["state"] = state
        return f"{authorize_base}?{urlencode(params)}"

    # ------------------------------------------------------------------
    # Orders (verified Daraz REST paths — migration doc treeId=754)
    # ------------------------------------------------------------------

    def get_orders(
        self,
        *,
        created_after: str,
        status: str = "ready_to_ship",
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "created_at",
        sort_direction: str = "ASC",
    ) -> dict[str, Any]:
        """
        GET /orders/get — list orders for the token's store.

        created_after example: 2026-01-01T00:00:00+05:00
        status filter examples: ready_to_ship, pending, packed, all
        """
        return self._request(
            "/orders/get",
            business_params={
                "created_after": created_after,
                "status": status,
                "limit": str(limit),
                "offset": str(offset),
                "sort_by": sort_by,
                "sort_direction": sort_direction,
            },
        )

    def get_order(self, order_id: int | str) -> dict[str, Any]:
        """GET /order/get — single order header/details."""
        return self._request("/order/get", business_params={"order_id": str(order_id)})

    def get_order_items(self, order_id: int | str) -> dict[str, Any]:
        """GET /order/items/get — line items with order_item_id, status, package_id, etc."""
        return self._request("/order/items/get", business_params={"order_id": str(order_id)})

    def get_multiple_order_items(self, order_ids: list[int | str]) -> dict[str, Any]:
        """GET /orders/items/get — up to 50 orders per call."""
        ids = ",".join(str(i) for i in order_ids)
        return self._request("/orders/items/get", business_params={"order_ids": f"[{ids}]"})

    # ------------------------------------------------------------------
    # Fulfillment / documents (verified legacy paths on Daraz platform)
    # ------------------------------------------------------------------

    def get_document(
        self,
        *,
        doc_type: str,
        order_item_ids: list[int | str],
    ) -> dict[str, Any]:
        """
        GET /order/document/get — invoice, shippingLabel, or carrierManifest.

        Returns base64-encoded document in data.document.file with mime_type.
        Order items must be packed/RTS — see official FAQ (E034 if not).
        """
        ids = ",".join(str(i) for i in order_item_ids)
        return self._request(
            "/order/document/get",
            business_params={
                "doc_type": doc_type,
                "order_item_ids": f"[{ids}]",
            },
        )

    def get_shipping_label(self, order_item_ids: list[int | str]) -> dict[str, Any]:
        """Convenience wrapper for doc_type=shippingLabel."""
        return self.get_document(doc_type="shippingLabel", order_item_ids=order_item_ids)

    @staticmethod
    def decode_document_file(document: dict[str, Any]) -> bytes:
        """Decode data.document.file from GetDocument response."""
        encoded = document.get("file") or document.get("File")
        if not encoded:
            raise ValueError("No file field in document response")
        return base64.b64decode(encoded)

    @staticmethod
    def save_document(document: dict[str, Any], output_path: str | Path) -> Path:
        """Write decoded document bytes to disk."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        mime = (document.get("mime_type") or document.get("MimeType") or "").lower()
        content = DarazClient.decode_document_file(document)
        if mime == "text/html" and not path.suffix:
            path = path.with_suffix(".html")
        elif "pdf" in mime and not path.suffix:
            path = path.with_suffix(".pdf")
        path.write_bytes(content)
        return path

    def get_shipment_providers(self) -> dict[str, Any]:
        """GET /shipment/providers/get — needed before pack on some flows."""
        return self._request("/shipment/providers/get")


def _demo_offline() -> None:
    """Run without credentials — shows authorize URL and signing only."""
    app_key = os.getenv("DARAZ_APP_KEY", "YOUR_APP_KEY")
    redirect = os.getenv("DARAZ_REDIRECT_URI", "http://localhost:8765/callback")
    url = DarazClient.build_authorize_url(app_key, redirect)
    print("=== Daraz Multi-Store POC (offline demo) ===")
    print()
    print("1. Register app at https://open.daraz.com/ (ERP category recommended)")
    print("2. Set callback URL in App Console to match DARAZ_REDIRECT_URI")
    print("3. Open this URL in a browser and authorize as seller:")
    print(url)
    print()
    print("4. Exchange ?code= for tokens:")
    print("   client.create_token_from_code(code)")
    print()
    print("5. Example signed GetOrders params (sanitized):")
    sample_params = {
        "app_key": "123456",
        "access_token": "SANITIZED_TOKEN",
        "timestamp": "1517820392000",
        "sign_method": "sha256",
        "created_after": "2026-01-01T00:00:00+05:00",
        "status": "ready_to_ship",
        "limit": "100",
        "offset": "0",
    }
    sign = DarazClient.sign("helloworld", "/orders/get", sample_params)
    print(json.dumps({**sample_params, "sign": sign[:16] + "..."}, indent=2))


def _demo_live() -> None:
    """Requires valid .env credentials."""
    client = DarazClient()
    created_after = os.getenv("POC_CREATED_AFTER", "2026-01-01T00:00:00+05:00")

    print("Fetching ready_to_ship orders...")
    orders_resp = client.get_orders(created_after=created_after, status="ready_to_ship", limit=10)
    orders = (orders_resp.get("data") or {}).get("orders") or []
    print(f"Found {len(orders)} orders in this page (countTotal may be higher).")

    for order in orders[:3]:
        order_id = order.get("order_id")
        print(f"\nOrder {order_id} statuses={order.get('statuses')}")
        items_resp = client.get_order_items(order_id)
        items = items_resp.get("data") or []
        item_ids = [i.get("order_item_id") for i in items if i.get("order_item_id")]
        print(f"  order_item_ids: {item_ids}")
        if item_ids:
            try:
                doc_resp = client.get_shipping_label(item_ids)
                doc = (doc_resp.get("data") or {}).get("document") or {}
                out = client.save_document(doc, Path("labels") / f"order_{order_id}_label")
                print(f"  Saved label -> {out} (mime={doc.get('mime_type')})")
            except DarazApiError as exc:
                print(f"  GetDocument skipped: [{exc.code}] {exc}")


if __name__ == "__main__":
    if os.getenv("DARAZ_APP_KEY") and os.getenv("DARAZ_APP_SECRET") and os.getenv("DARAZ_ACCESS_TOKEN"):
        _demo_live()
    else:
        _demo_offline()
