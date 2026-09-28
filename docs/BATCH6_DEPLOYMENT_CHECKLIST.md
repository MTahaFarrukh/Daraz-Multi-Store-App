# Batch 6 — Deployment & Live Test Checklist

Backend is authoritative for tenancy. `src/db/rls.sql` is **defense in depth** and is **not** claimed as verified on hosted Supabase.

## S. Ordered deployment steps

1. **Backup / git checkpoint** — tag `pre-batch6-deploy`, snapshot DB.
2. **Schema application** — deploy with `DATABASE_URL`; startup runs idempotent `schema.sql` (`CREATE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` only). Optionally review `src/db/rls.sql` before applying manually.
3. **Environment validation** — production must set `ENVIRONMENT=production`, `DATABASE_URL`, `DARAZ_TOKEN_KEY`, `SUPABASE_URL`. Reject `AUTH_TEST_MODE=true` and `TENANCY_REPO=memory`.
4. **Build** — `npm ci && npm run build` in `frontend/`; Docker uses frontend stage + Python image with Chromium.
5. **Deploy** — roll out container/service; confirm `/api/public-config` and SPA index load.
6. **Health checks** — process up, schema ensure succeeded, no production guard errors in logs.
7. **Auth smoke** — login/signup, `/api/me` returns workspace + capabilities.
8. **Store connection smoke** — OAuth start/callback or existing store list.
9. **RTS** — Shipping Load RTS for selected stores.
10. **Printing** — print unprinted; confirm history; Retry Failed if needed.
11. **Public Product Add** — URL → destinations → verify statuses.
12. **Connected Product Add** — trusted connection + copy/add path.
13. **Retry / Reconcile** — Needs attention / Needs reconciliation actions.
14. **Inventory** — Sync Products; Unknown vs 0 stock.
15. **Analytics** — month selector; Gross Sales ≠ profit; MoM N/A when no prior data.
16. **Finance** — Sync Finance (explicit stores); Gross Sales from orders; Known Fees/Payouts.
17. **Connections** — request/accept; revoke confirm; permission toggles.
18. **Audit activity** — owner/admin readable humanized actions.

## T. Rollback criteria

Roll back immediately if:

- Production startup fails env validation or schema ensure
- Auth/login broken for all users
- Print jobs mark success without recoverable history (or mass false SUCCESS)
- Finance Gross Sales clearly double-counting vs orders after deploy
- Data corruption / cross-workspace leakage observed

Rollback: previous image + prior DB snapshot if schema-incompatible (Batch 6 schema adds only indexes/tables IF NOT EXISTS — forward-safe).

## U. Consolidated live test matrix

| Feature | Test | Expected | Pass/Fail |
|---------|------|----------|-----------|
| Auth | Login + `/api/me` | Workspace + caps | ☐ |
| Stores | List + rename | Persist display name | ☐ |
| RTS | Load selected stores | Orders; empty ≠ all | ☐ |
| Print | Print unprinted | PDF + history; Retry Failed clear | ☐ |
| Print interrupt | Kill mid-job | Interrupted warning + recovery | ☐ |
| Product Add URL | Multi-dest | Per-store human statuses | ☐ |
| Product Add connected | Peer source | Caps enforced | ☐ |
| Retry/Reconcile | Failed / needs recon | Human labels; safe actions | ☐ |
| Inventory | Sync + filters | Unknown ≠ 0; pagination | ☐ |
| Analytics | Month MoM | N/A if no prior; Gross Sales label | ☐ |
| Finance sync | 2 stores (1 fail) | Partial warning; no Rs 0 unknowns | ☐ |
| Finance Gross Sales | Orders vs ledger | Equals orders sum only | ☐ |
| Connections | Revoke + perms | Confirm dialogs; code copy | ☐ |
| Audit | Owner view | Humanized actions; no raw JSON | ☐ |
| RBAC | Viewer finance | 403 sync/read | ☐ |

## V. Remaining LIVE-only unknowns

- Exact Daraz finance payload variants per marketplace
- Supabase RLS applied/verified in hosted project
- Real Chromium HTML→PDF conversion under load
- OAuth callback URL / cookie domain on production host
