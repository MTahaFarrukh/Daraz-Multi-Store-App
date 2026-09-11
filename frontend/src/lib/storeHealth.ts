import type { StoreView } from "@/types/api";

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

export function statusLabel(store: StoreView): string {
  return storeStatus(store) === "needs_reconnection" ? "Needs reconnection" : "Connected";
}

export function statusTone(store: StoreView): "ok" | "warn" | "danger" | "muted" {
  if (storeStatus(store) === "needs_reconnection") return "danger";
  if (store.needs_attention === true) return "warn";
  if (store.needs_attention === false) return "ok";
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
