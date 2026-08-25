# Phase 3 CLI

Minimal multi-store integration: encrypted local tokens, refresh, fetch orders/labels, merge printable PDF.

## Prerequisites

1. OAuth at least one seller via `uvicorn src.app:app` ? `/oauth/login`.
2. `pip install -r requirements.txt` (includes `cryptography`).
3. For HTML shipping labels ? PDF merge: **Microsoft Edge** or **Google Chrome** installed (headless `--print-to-pdf`). WeasyPrint is an optional fallback.

## Token file

`data/tokens.json` is Fernet-encrypted (`DMST1:...`). Decrypted shape:

```json
{
  "stores": [
    {
      "store_id": "seller_gmail_com",
      "store_name": "seller@gmail.com",
      "account": "seller@gmail.com",
      "access_token": "...",
      "refresh_token": "...",
      "access_token_expires_at": "..."
    }
  ]
}
```

- Key: `DARAZ_TOKEN_KEY` env, or auto-created `data/.token_key` (gitignored).
- Phase 2 single-record files migrate automatically on first load.
- Access tokens last ~30 days; refresh tokens ~180 days. Use `refresh-tokens` before access expiry.

## Commands

```powershell
python -m src.cli list-stores
python -m src.cli refresh-tokens
python -m src.cli refresh-tokens --force
python -m src.cli fetch-orders --limit 5
python -m src.cli fetch-labels --limit 3
python -m src.cli print-all --limit 3
python -m src.cli print-all --reuse-saved --output data/output/combined-labels.pdf
```

| Flag | Meaning |
|------|---------|
| `--store` | Store id or account email |
| `--limit` | Max orders per store (default 10) |
| `--status` | Order status filter (default `ready_to_ship`) |
| `--created-after` | ISO8601 lower bound (default ~30 days ago) |

Labels land in `data/labels/{store_id}/{order_id}__{item_id}.html|pdf`.  
Merged PDF: `data/output/combined-labels.pdf`.

## Connect another store

Run `/oauth/login` again with the other seller account. Tokens **upsert** by account - they do not wipe the first store.

## Security

- Never commit `.env`, `data/tokens.json`, or `data/.token_key`.
- CLI/API sanitize views never print access or refresh tokens.
- Does **not** call `/order/pack` or `/order/rts`.
