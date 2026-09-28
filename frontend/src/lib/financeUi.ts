/** Finance UI helpers — never coerce unknown amounts to Rs 0. */

export function formatFinanceAmount(
  value: number | null | undefined,
  currency = "PKR"
): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${currency} ${Number(value).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}`;
}

export function financeNotSynced(summary: {
  last_synced?: string | null;
  transaction_count?: number;
  payout_count?: number;
} | null | undefined): boolean {
  if (!summary) return true;
  if (summary.last_synced) return false;
  return !(summary.transaction_count || summary.payout_count);
}

export function financePartialWarning(result: {
  partial?: boolean;
  status?: string;
} | null | undefined): string | null {
  if (!result) return null;
  if (result.partial || result.status === "partial") {
    return "Some stores could not be synced.";
  }
  return null;
}

export const FINANCE_EMPTY_MESSAGE = "Finance data has not been synced yet.";
export const FINANCE_PARTIAL_WARNING = "Some stores could not be synced.";
