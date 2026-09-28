/** Analytics display formatting — MoM null → N/A, never infinite %. */

export function formatMoneyAmount(
  value: number | null | undefined,
  currency = "PKR"
): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${currency} ${Number(value).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}`;
}

export function formatMomPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  const n = Number(value);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

export function formatCount(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString();
}

export const ANALYTICS_EMPTY_MESSAGE = "No order data for this month.";
