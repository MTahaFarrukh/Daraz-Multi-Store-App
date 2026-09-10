# Phase 1A setup — Supabase Auth + workspaces (modern API keys)

## Manual steps in Supabase

1. Create a Supabase project.
2. **Authentication → Providers → Email** — enable Email.
   - For local/dev: disable “Confirm email” (or use the confirmation link).
3. **Settings → API**
   - Copy **Project URL** → `SUPABASE_URL`
4. **Settings → API Keys** (new key system)
   - Copy **Publishable** key → `SUPABASE_PUBLISHABLE_KEY` (browser-safe)
   - Copy **Secret** key → `SUPABASE_SECRET_KEY` (**server only** — never put in frontend)
5. **Settings → Database** — copy connection string → `DATABASE_URL`
6. Run the SaaS schema (app also applies it on startup when `DATABASE_URL` is set):

```bash
psql "$DATABASE_URL" -f src/db/schema.sql
```

7. Optional defense-in-depth RLS (only if the API DB role is `authenticated`; skip if using a privileged server role):

```bash
psql "$DATABASE_URL" -f src/db/rls.sql
```

8. Put values in `.env` (see `.env.example`). Never commit secrets.

### How auth tokens are verified

The backend validates Supabase **user access tokens** using the project JWKS endpoint:

```text
{SUPABASE_URL}/auth/v1/.well-known/jwks.json
```

Checks include signature, expiry (`exp`), issuer (`{SUPABASE_URL}/auth/v1`), and audience (`authenticated`).

- `SUPABASE_PUBLISHABLE_KEY` — frontend Supabase JS client only  
- `SUPABASE_SECRET_KEY` — server-only; **not** used as a user JWT and **not** exposed to the browser  
- `SUPABASE_JWT_SECRET` — **legacy optional fallback** only for older projects that still issue HS256 user JWTs. New projects should not need it.

## Bootstrap an owner + import legacy stores

1. Sign up at `/login` (creates workspace + owner membership via `/api/bootstrap`).
2. Note your workspace id from `GET /api/me` (or browser Network tab).
3. Back up and import the old vault **into that owner workspace only**:

```powershell
$env:ALLOW_LEGACY_DATA_IMPORT="true"
python -m scripts.migrate_legacy_stores --workspace-id YOUR_WORKSPACE_UUID
```

Or:

```http
POST /api/admin/import-legacy-stores
Authorization: Bearer <access_token>
X-Workspace-Id: <workspace_id>
{"confirm": true}
```

4. Set `ALLOW_LEGACY_DATA_IMPORT=false` again (default).

The legacy `data/tokens.json` / `daraz_app_kv.stores_v1` blob is **not deleted**.

## Run locally

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn src.app:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/login
