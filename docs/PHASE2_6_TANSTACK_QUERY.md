# Phase 2.6 — TanStack Query Server-State Standard

## What belongs in TanStack Query

**Server resource state** fetched from FastAPI:

- Stores
- Store groups
- Store performance (months + leaderboard)
- Print job history
- RTS orders **only after** explicit Load Orders

## What does NOT belong here

| Kind | Owner |
|------|--------|
| Supabase session / login / logout | `useAuth` |
| `/api/bootstrap`, `/api/me` | `useAuth` (auth lifecycle) |
| Sidebar, dialogs, tabs, search, sort, view prefs | React state / localStorage |
| Shipping store checkboxes | local state (+ localStorage) |
| Print job polling loop | imperative (Shipping) |
| PDF download click | imperative `Api.downloadPrint` |
| OAuth redirect | imperative `Api.oauthStart` |

TanStack Query is a **frontend cache**. FastAPI remains the authz boundary.

## Query key conventions

All workspace resources:

```ts
["workspace", workspaceId, "<resource>", ...filters]
```

Examples:

- `["workspace", wid, "stores"]`
- `["workspace", wid, "store-groups"]`
- `["workspace", wid, "store-performance", year, month, metric]`
- `["workspace", wid, "print-jobs"]`
- `["workspace", wid, "orders", status, limit, sortedStoreIds]`

Future communities (not implemented):

```ts
["community", communityId, ...]
```

Never reuse workspace keys for community data. Community membership ≠ workspace membership.

## Workspace isolation

- Every tenant key includes `workspaceId`.
- Switching workspace (future) uses different keys → no cross-workspace cache reuse.
- Logout / identity change calls `queryClient.clear()`.

## Invalidation map

| Mutation | Invalidates |
|----------|-------------|
| Rename store | `stores`, all `store-performance` for workspace |
| Refresh connection | `stores` |
| Create/update/delete group | `store-groups` |
| Import browser profiles | `store-groups` |
| Performance sync | `store-performance` for that year/month + months list |
| Print job complete | `print-jobs` |

Avoid blanket `invalidateQueries()` with no key.

## Stale / retry policy

Defaults (`src/lib/queryClient.ts`):

- `staleTime` 30s default; stores/groups 60s; print jobs 15s; performance 30s
- `refetchOnWindowFocus: false`
- Retry: **never** on 401/403/4xx; at most **1** retry for other errors
- Mutations: no retry

**Important:** GET performance reads our DB snapshot. Only POST `/api/store-performance/sync` contacts Daraz. Query refetch ≠ Daraz resync.

## Shipping / RTS

- Mounting Shipping loads stores/groups only (shared cache).
- Orders query stays `enabled: false` until Load Orders.
- Empty selection never enables the orders query.
- Changing selection/limit disables the query until the next Load click.

## Logout cleanup

`useAuth` clears the QueryClient on:

- sign out
- signed-out auth event
- sign-in / sign-up (wipe before new identity)
- user or workspace id change during refresh

## Source of truth files

- `src/lib/queryClient.ts`
- `src/lib/queryKeys.ts`
- `src/hooks/queries/*`
- `src/hooks/mutations/*`
