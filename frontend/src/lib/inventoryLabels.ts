/** Inventory stock display helpers — unknown must never become 0. */

export type StockBadge = "Unknown" | "Out of Stock" | "Low Stock" | "In Stock";

export function stockBadgeForQuantity(
  quantity: number | null | undefined,
  lowStockThreshold = 5
): StockBadge {
  if (quantity == null || Number.isNaN(Number(quantity))) return "Unknown";
  const q = Number(quantity);
  if (q <= 0) return "Out of Stock";
  if (q <= lowStockThreshold) return "Low Stock";
  return "In Stock";
}

export function formatStockQuantity(quantity: number | null | undefined): string {
  if (quantity == null || Number.isNaN(Number(quantity))) return "Unknown";
  return String(Number(quantity));
}

export function stockBadgeTone(
  badge: StockBadge
): "ok" | "warn" | "danger" | "muted" {
  if (badge === "In Stock") return "ok";
  if (badge === "Low Stock") return "warn";
  if (badge === "Out of Stock") return "danger";
  return "muted";
}

export const INVENTORY_EMPTY_MESSAGE =
  "No inventory data yet. Sync Products to populate listings.";
