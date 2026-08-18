# Daraz Multi-Store Manager — Investigation POC

Proof-of-concept for investigating the **official Daraz Open Platform API (Pakistan)** before building a multi-store dashboard.

**Full findings:** [docs/DARAZ_API_FINDINGS.md](docs/DARAZ_API_FINDINGS.md)

## What this is

- Minimal Python client with HMAC-SHA256 signing
- Placeholders for credentials via `.env`
- No frontend, database, or deployment
- Does **not** call Daraz unless you add real credentials

## What this is not

- Not the final application
- Not tested against live Daraz PK data (requires your app approval + seller OAuth)

## Requirements

- Python 3.12+
- Daraz Open Platform app ([open.daraz.com](https://open.daraz.com/))

## Setup

```powershell
cd daraz-multi-store
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env with your App Key and App Secret (never commit .env)
```

## OAuth (manual step)

1. Register callback URL in App Console (e.g. `http://localhost:8765/callback`).
2. Run offline demo to print authorize URL:

```powershell
python -m src.daraz_api
```

3. Open the URL, sign in as seller, approve the app.
4. Capture `code` from redirect URL.
5. Exchange code (Python shell):

```python
from src.daraz_api import DarazClient
client = DarazClient()
tokens = client.create_token_from_code("PASTE_CODE_HERE")
print(tokens)
```

6. Save `access_token` and `refresh_token` to `.env`.

## Live smoke test (after tokens)

Set `DARAZ_ACCESS_TOKEN` in `.env`, then:

```powershell
python -m src.daraz_api
```

Optional: set `POC_CREATED_AFTER=2026-01-01T00:00:00+05:00`

This attempts:

- `GetOrders` with `status=ready_to_ship`
- `GetOrderItems` for first orders
- `GetDocument` shipping labels (saved under `labels/`)

## Project layout

```
daraz-multi-store/
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── src/
│   ├── __init__.py
│   └── daraz_api.py
└── docs/
    └── DARAZ_API_FINDINGS.md
```

## Key official endpoints (Pakistan)

| Purpose | Path |
|---------|------|
| REST base | `https://api.daraz.pk/rest` |
| OAuth | `https://api.daraz.pk/oauth/authorize` |
| Orders | `/orders/get` |
| Order items | `/order/items/get` |
| Shipping label | `/order/document/get` (`doc_type=shippingLabel`) |

## Support

Official API support: **support-api@daraz.pk**
