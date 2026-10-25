import type { ApiHealthStatus, StoreView, TokenStatus } from "@/types/api";

/** Prefer backend connection_status; fall back only from known expiry seconds. */
export function storeStatus(store: StoreView): "connected" | "needs_reconnection" {
  if (store.connection_status === "needs_reconnection") return "needs_reconnection";
  if (store.connection_status === "connected") return "connected";
  if (
    store.refresh_token_expires_in_seconds != null &&
    store.refresh_token_expires_in_seconds === 0
  ) {
    return "needs_reconnection";
  }
  return "connected";
}

export function tokenStatus(store: StoreView): TokenStatus {
  if (store.token_status === "ok" || store.token_status === "expiring" || store.token_status === "expired" || store.token_status === "unknown") {
    return store.token_status;
  }
  if (storeStatus(store) === "needs_reconnection") return "expired";
  if (store.needs_attention === true) return "expiring";
  if (store.needs_attention === false) return "ok";
  return "unknown";
}

export function apiHealth(store: StoreView): ApiHealthStatus {
  const v = store.api_health;
  if (v === "ok" || v === "error" || v === "skipped" || v === "unverified") return v;
  return "unverified";
}

/** Human label — never call token-only state "Healthy / API OK". */
export function statusLabel(store: StoreView): string {
  if (storeStatus(store) === "needs_reconnection") return "Needs reconnection";
  const api = apiHealth(store);
  if (api === "ok") return "API verified";
  if (api === "error") return "API check failed";
  const tok = tokenStatus(store);
  if (tok === "ok") return "Token OK (unverified)";
  if (tok === "expiring") return "Token expiring";
  if (tok === "unknown") return "Token expiry unknown";
  return "Connected";
}

export function statusTone(store: StoreView): "ok" | "warn" | "danger" | "muted" {
  if (storeStatus(store) === "needs_reconnection") return "danger";
  if (apiHealth(store) === "error") return "danger";
  if (apiHealth(store) === "ok") return "ok";
  if (store.needs_attention === true) return "warn";
  if (store.needs_attention === false) return "muted"; // token OK ≠ verified healthy
  return "muted";
}

export function storeTitle(store: StoreView): string {
  return store.display_name || store.shop_name || store.store_name || store.store_id;
}

export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const diff = Date.now() - t;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export function tokenDaysLeft(store: StoreView): number | null {
  if (store.access_token_expires_in_seconds == null) return null;
  return Math.max(0, Math.round(store.access_token_expires_in_seconds / 86400));
}
