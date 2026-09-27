"""Phase 4D.4 — CreateProduct must not put XML payload in the URL (HTTP 414)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.daraz_api import DarazApiError, DarazClient
from src.product_create_payload import build_create_product_xml


def _client() -> DarazClient:
    return DarazClient(
        app_key="appkey123",
        app_secret="secret456",
        access_token="token789",
        api_base="https://api.daraz.pk/rest",
    )


def _large_xml() -> str:
    """Realistic oversized CreateProduct XML (description + images + variants)."""
    long_desc = "<p>" + ("Product detail paragraph. " * 400) + "</p>"
    images = "".join(
        f"<Image>https://static-01.daraz.pk/p/img{i:03d}_"
        f"{'x' * 40}.jpg</Image>"
        for i in range(8)
    )
    skus = ""
    for i in range(12):
        skus += (
            f"<Sku><SellerSku>MTF-TEST-{i:03d}-ABCD1234</SellerSku>"
            f"<quantity>5</quantity><price>{100 + i}.00</price>"
            f"<package_weight>0.50</package_weight>"
            f"<package_length>10</package_length>"
            f"<package_width>10</package_width>"
            f"<package_height>5</package_height>"
            f"<saleProp><Color>Color{i}</Color></saleProp></Sku>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Request><Product>"
        "<PrimaryCategory>12345</PrimaryCategory>"
        f"<Attributes><name>Test</name><description>{long_desc}</description>"
        "<brand>No Brand</brand></Attributes>"
        f"<Images>{images}</Images>"
        f"<Skus>{skus}</Skus>"
        "</Product></Request>"
    )


def test_create_product_uses_form_not_query_for_large_payload():
    """Large XML must travel in form body; URL stays bounded."""
    client = _client()
    xml = _large_xml()
    assert len(xml) > 8000  # would blow typical URI limits if query-encoded

    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = str(url)
        captured["params"] = kwargs.get("params")
        captured["data"] = kwargs.get("data")
        captured["content"] = kwargs.get("content")
        captured["headers"] = kwargs.get("headers") or {}
        req = httpx.Request("POST", url, data=kwargs.get("data"))
        return httpx.Response(
            200,
            json={"code": "0", "data": {"item_id": "42"}},
            request=req,
        )

    with patch("httpx.Client") as client_cls:
        mock_http = MagicMock()
        mock_http.__enter__.return_value = mock_http
        mock_http.__exit__.return_value = False
        mock_http.post.side_effect = fake_post
        client_cls.return_value = mock_http
        result = client.create_product(xml)

    assert result["code"] == "0"
    mock_http.post.assert_called_once()
    # No query params with the XML
    assert captured["params"] in (None, {})
    assert captured["data"] is not None
    assert "payload" in captured["data"]
    assert captured["data"]["payload"] == xml
    assert "sign" in captured["data"]
    assert "access_token" in captured["data"]
    ct = captured["headers"].get("Content-Type", "")
    assert "application/x-www-form-urlencoded" in ct
    # URL itself must stay short (no giant query string)
    parsed = urlparse(captured["url"])
    assert parsed.path.endswith("/product/create")
    assert len(captured["url"]) < 200
    assert len(parsed.query) < 50


def test_create_product_signing_includes_payload_param_not_json_body():
    """Form CreateProduct: sign covers payload as a parameter; no JSON body append."""
    client = _client()
    xml = "<Request><Product><PrimaryCategory>1</PrimaryCategory></Product></Request>"

    # Compute expected sign the same way create_product will
    from unittest.mock import patch as _patch

    with _patch("httpx.Client") as client_cls:
        mock_http = MagicMock()
        mock_http.__enter__.return_value = mock_http
        mock_http.__exit__.return_value = False
        captured_data: dict = {}

        def fake_post(url, **kwargs):
            captured_data.update(kwargs.get("data") or {})
            req = httpx.Request("POST", url)
            return httpx.Response(
                200, json={"code": "0", "data": {}}, request=req
            )

        mock_http.post.side_effect = fake_post
        client_cls.return_value = mock_http

        with _patch.object(DarazClient, "_common_params", return_value={
            "app_key": "appkey123",
            "sign_method": "sha256",
            "timestamp": "1700000000000",
        }):
            client.create_product(xml)

    assert captured_data["payload"] == xml
    expected_params = {
        "app_key": "appkey123",
        "sign_method": "sha256",
        "timestamp": "1700000000000",
        "access_token": "token789",
        "payload": xml,
    }
    expected_sign = DarazClient.sign(
        "secret456", "/product/create", expected_params, body=None
    )
    assert captured_data["sign"] == expected_sign
    # Must NOT match a sign that incorrectly appends XML as JSON body
    wrong = DarazClient.sign(
        "secret456", "/product/create", expected_params, body=xml
    )
    assert captured_data["sign"] != wrong


def test_legacy_query_post_would_exceed_uri_limit():
    """Demonstrate old construction: payload in params → URL length >> 8k."""
    xml = _large_xml()
    params = {
        "app_key": "appkey123",
        "access_token": "token789",
        "timestamp": "1700000000000",
        "sign_method": "sha256",
        "payload": xml,
        "sign": "ABCD",
    }
    # Simulate httpx query encoding length
    from urllib.parse import urlencode

    query = urlencode(params)
    url = f"https://api.daraz.pk/rest/product/create?{query}"
    assert len(url) > 8000


def test_create_product_simple_payload_still_works():
    client = _client()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Request><Product><PrimaryCategory>1</PrimaryCategory>"
        "<Attributes><name>Simple</name><brand>No Brand</brand></Attributes>"
        "<Skus><Sku><SellerSku>MTF-1</SellerSku><quantity>1</quantity>"
        "<price>99</price></Sku></Skus></Product></Request>"
    )
    with patch("httpx.Client") as client_cls:
        mock_http = MagicMock()
        mock_http.__enter__.return_value = mock_http
        mock_http.__exit__.return_value = False

        def fake_post(url, **kwargs):
            assert kwargs.get("data") is not None
            assert "payload" in kwargs["data"]
            req = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"code": "0", "data": {"item_id": "9"}},
                request=req,
            )

        mock_http.post.side_effect = fake_post
        client_cls.return_value = mock_http
        out = client.create_product(xml)
    assert out["data"]["item_id"] == "9"


def test_non_json_414_diagnostics_are_safe():
    client = _client()
    xml = _large_xml()

    with patch("httpx.Client") as client_cls:
        mock_http = MagicMock()
        mock_http.__enter__.return_value = mock_http
        mock_http.__exit__.return_value = False

        def fake_post(url, **kwargs):
            # Simulate gateway HTML 414 after form post (unlikely) or misconfig
            req = httpx.Request("POST", "https://api.daraz.pk/rest/product/create")
            return httpx.Response(
                414,
                text="<html>Request-URI Too Large</html>",
                headers={"content-type": "text/html"},
                request=req,
            )

        mock_http.post.side_effect = fake_post
        client_cls.return_value = mock_http
        with pytest.raises(DarazApiError) as ei:
            client.create_product(xml)

    err = ei.value
    assert err.http_status == 414
    assert "url_len=" in str(err)
    assert "body_len=" in str(err)
    assert "transport=form" in str(err)
    assert "access_token" not in str(err).lower() or "token789" not in str(err)
    assert err.diagnostics.get("content_type") == "text/html"
    assert "token789" not in (err.diagnostics.get("response_preview") or "")


def test_build_create_product_xml_fixture_is_large_enough():
    draft = {
        "product": {
            "title": "Glow Toy",
            "title_en": "Glow Toy",
            "primary_category_id": 123,
            "brand": "No Brand",
            "attributes": {"name": "Glow Toy"},
        },
        "media": {
            "product_images": [
                f"https://static-01.daraz.pk/p/{i}.jpg" for i in range(6)
            ],
            "resolved_images": [
                f"https://static-01.daraz.pk/p/{i}.jpg" for i in range(6)
            ],
        },
        "variants": [
            {
                "seller_sku": f"MTF-{i}",
                "price": 100 + i,
                "quantity": 3,
                "package_weight": 0.5,
                "package_length": 10,
                "package_width": 10,
                "package_height": 5,
                "sale_props": {"Color": f"C{i}"},
            }
            for i in range(5)
        ],
    }
    # Inflate description like real public pages
    draft["product"]["description_html"] = "<p>" + ("detail " * 2000) + "</p>"
    xml = build_create_product_xml(draft)
    assert "PrimaryCategory" in xml
    assert len(xml) > 5000
