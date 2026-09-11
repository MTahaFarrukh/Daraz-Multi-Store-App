# Legacy frontend → React migration checklist (Phase 1B)

| Legacy feature | Source | React destination | Status |
|----------------|--------|-------------------|--------|
| Login / signup | `login.html`, `auth.js` | `/login`, `/signup` | Done |
| Session persist + Bearer | `auth.js` | `lib/supabase`, `lib/api` | Done |
| Bootstrap workspace | `auth.js` → `/api/bootstrap` | `AuthProvider` | Done |
| Public config | `/api/public-config` | `lib/supabase` | Done |
| Logout | `app.js` | Header account menu | Done |
| Store list + token status | `app.js` | Stores + Shipping selector | Done |
| Rename store | `PATCH /api/stores/{id}` | Stores page | Done |
| Connect store OAuth | `/api/oauth/start` | Stores page | Done |
| All / None selection | `app.js` | StoreSelector | Done |
| Store groups CRUD | `/api/store-groups*` | Stores page | Done |
| Browser profile import | `/api/store-groups/import-browser` | Stores one-time import | Done |
| Selection persistence | localStorage | Shipping local preference | Done |
| Load RTS orders | `GET /api/orders` | Shipping Ready to Ship | Done |
| Order limit | `#limit-select` | Shipping controls | Done |
| Print labels + poll | `POST` + status | Shipping print flow | Done |
| PDF download (auth) | job download | Shipping download | Done |
| Last print table | label_details | Shipping success panel | Done |
| Print history list | n/a | Shipping tab + `GET /api/print-jobs` | Done |
| Refresh tokens | `POST /api/refresh-tokens` | Stores page | Done |
| Toast / busy | `app.js` | banners + busy banner | Done |
| Legacy dashboard shell | `index.html` | App shell sidebar | Done |

## Deferred / not available from API
| Desired UI | Reason |
|------------|--------|
| New Orders Today / Revenue cards | No backend metrics — Overview shows unavailable |
| Analytics graphs | Phase 8+ |
| Order-level print selection | Print API is store+limit based, not order IDs |
| Multi-workspace switcher | Memberships returned; switcher deferred |

## Legacy UI
Static files under `src/static/` remain for reference / `SERVE_LEGACY_UI=true`.
Production serves the Vite SPA from `frontend/dist` via FastAPI.
