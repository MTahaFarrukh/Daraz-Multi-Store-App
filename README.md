# Daraz Multi-Store Manager — Phase 2 Live Verification

Phase 1 investigation: [docs/DARAZ_API_FINDINGS.md](docs/DARAZ_API_FINDINGS.md)  
Phase 2 test report: [docs/PHASE2_LIVE_TEST.md](docs/PHASE2_LIVE_TEST.md)  
Label processing (Mini Phase 3A): [docs/LABEL_PROCESSING.md](docs/LABEL_PROCESSING.md) | [docs/MINI_PHASE_3A_REPORT.md](docs/MINI_PHASE_3A_REPORT.md)

## Phase 2 objective

Connect **one** real Daraz Pakistan seller store via OAuth and verify:

> Can we retrieve a real shipping label using `GET /order/document/get` with `doc_type=shippingLabel`?

**Read-only.** Does **not** call `/order/pack` or `/order/rts`. Does **not** modify orders.

## Requirements

- Python 3.12+
- Daraz Open Platform app ([open.daraz.com](https://open.daraz.com/))
- App callback URL: `http://127.0.0.1:8000/oauth/callback` (must match App Console exactly)

## Setup

```powershell
cd daraz-multi-store
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env — set DARAZ_APP_KEY and DARAZ_APP_SECRET only (never commit .env)
```

Register the callback URL in **App Console → Manage → Callback URL**.

## Run OAuth + smoke test

```powershell
uvicorn src.app:app --reload --host 127.0.0.1 --port 8000
```

1. Open http://127.0.0.1:8000/
2. Click **Connect seller store** → `/oauth/login` (Daraz browser login)
3. After redirect, tokens are saved to `data/tokens.json` (gitignored)
4. Open http://127.0.0.1:8000/test/live — runs live smoke test
5. Review `docs/PHASE2_LIVE_TEST.md` and `data/test-label.pdf` or `.html`

CLI alternative (after OAuth):

```powershell
python -m src.smoke_test
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | POC info page |
| GET | `/oauth/login` | Redirect to Daraz OAuth |
| GET | `/oauth/callback` | Exchange code → save tokens |
| GET | `/stores` | Sanitized store info (no tokens) |
| GET | `/test/live` | Read-only smoke test + report |

## Security

- Tokens stored in `data/tokens.json` (development only, gitignored)
- App Secret, access tokens, and refresh tokens are **never** logged or returned by API
- `/stores` returns account, seller_id, country, expiry only

## Project layout

```
daraz-multi-store/
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── data/                  # gitignored — tokens + test label
│   ├── tokens.json
│   └── test-label.*
├── src/
│   ├── app.py             # FastAPI OAuth + test server
│   ├── daraz_api.py       # Signed Daraz client
│   ├── token_store.py     # Local token JSON
│   ├── smoke_test.py      # Live test + report writer
│   └── config.py
└── docs/
    ├── DARAZ_API_FINDINGS.md
    └── PHASE2_LIVE_TEST.md
```

## Support

Official API support: **support-api@daraz.pk**

## Label processing (local — no Daraz API)

While developer registration is pending, test the label merge engine:

```powershell
python -m src.label_cli generate-test-labels
python -m src.label_cli list-labels
python -m src.label_cli merge-labels
pytest
```

Output: `data/output/combined-test-labels.pdf` (5 pages from 5 synthetic store labels).
