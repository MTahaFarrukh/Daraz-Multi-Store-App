# Daraz Multi-Store Manager

Phase 1 investigation: [docs/DARAZ_API_FINDINGS.md](docs/DARAZ_API_FINDINGS.md)  
Phase 2 test report: [docs/PHASE2_LIVE_TEST.md](docs/PHASE2_LIVE_TEST.md)  
Phase 3 CLI: [docs/PHASE3_CLI.md](docs/PHASE3_CLI.md)  
Label processing (Mini Phase 3A): [docs/LABEL_PROCESSING.md](docs/LABEL_PROCESSING.md)

## Requirements

- Python 3.12+
- Daraz Open Platform app ([open.daraz.com](https://open.daraz.com/))
- Callback URL must match `DARAZ_REDIRECT_URI` exactly (ngrok or `http://127.0.0.1:8000/oauth/callback`)
- Edge or Chrome recommended for HTML→PDF when merging live labels

## Setup

```powershell
cd daraz-multi-store
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env — set DARAZ_APP_KEY and DARAZ_APP_SECRET only (never commit .env)
```

## Web UI

```powershell
uvicorn src.app:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ — dashboard to connect stores, load ready-to-ship orders, and print a combined shipping-label PDF.

OAuth callback returns to the dashboard. For ngrok callbacks, keep `DARAZ_REDIRECT_URI` pointing at your tunnel `/oauth/callback`.


## Phase 3 CLI

```powershell
python -m src.cli list-stores
python -m src.cli refresh-tokens
python -m src.cli fetch-orders --limit 5
python -m src.cli fetch-labels --limit 3
python -m src.cli print-all --limit 3
```

See [docs/PHASE3_CLI.md](docs/PHASE3_CLI.md).

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | POC info page |
| GET | `/oauth/login` | Redirect to Daraz OAuth |
| GET | `/oauth/callback` | Exchange code → upsert store tokens |
| GET | `/stores` | Sanitized store list (no tokens) |
| GET | `/test/live` | Read-only smoke test + report |

## Security

- Tokens encrypted at rest (`data/tokens.json` + `data/.token_key` or `DARAZ_TOKEN_KEY`)
- App Secret, access tokens, and refresh tokens are **never** logged or returned by API
- Access token ~30 days; refresh token ~180 days

## Local label merge (synthetic, no Daraz)

```powershell
python -m src.label_cli generate-test-labels
python -m src.label_cli list-labels
python -m src.label_cli merge-labels
pytest
```

## Support

Official API support: **support-api@daraz.pk**
