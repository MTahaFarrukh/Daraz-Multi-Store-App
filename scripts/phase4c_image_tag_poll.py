"""Verify whether /images/migrate with singular <Image> batch_id polls OK."""

from __future__ import annotations

import json
import time

from src.daraz_api import DarazApiError, DarazClient
from src.token_store import list_stores


def main() -> None:
    c = DarazClient(access_token=list_stores()[0]["access_token"])
    img = "https://static-01.daraz.pk/p/b35e75a2b2609e4071729383467e6f6c.png"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Request><Image><Url>{img}</Url></Image></Request>"
    )
    data = c._request("/images/migrate", method="POST", business_params={"payload": xml})
    bid = data.get("batch_id")
    print("Image-tag batch", bid)
    time.sleep(1.0)
    try:
        r = c._request("/image/response/get", business_params={"batch_id": bid})
        print("poll ok", json.dumps({"code": r.get("code"), "data": r.get("data")}))
    except DarazApiError as e:
        print("poll fail", e.code, str(e)[:160], json.dumps(e.payload or {})[:300])


if __name__ == "__main__":
    main()
