/** Per-store Add Product progress labels + Retry vs Reconcile helpers. */

export type DestinationAttemptLike = {
  status?: string | null;
  creation_status?: string | null;
  attempt_state?: string | null;
  attempt_id?: string | null;
  reason?: string | null;
  item_id?: string | null;
  retry_safe?: { item_id?: string; step?: string } | null;
};

const PROGRESS_LABELS: Record<string, string> = {
  PREPARING: "Preparing",
  VALIDATING: "Validating",
  PREPARING_IMAGES: "Preparing images",
  CREATING: "Creating",
  CREATED_UNVERIFIED: "Verifying",
  VERIFYING: "Verifying",
  VERIFIED: "Verified",
  CREATED: "Verified",
  Active: "Verified",
  Created: "Verified",
  "Pending QC": "Verified",
  NEEDS_ATTENTION: "Needs attention",
  NEEDS_RECONCILIATION: "Needs reconciliation",
  FAILED_SAFE_TO_RETRY: "Failed",
  FAILED: "Failed",
  Failed: "Failed",
  READY: "Validating",
  ALREADY_EXISTS: "Verified",
  POSSIBLE_DUPLICATE: "Needs attention",
  BLOCKED_CREATE: "Validating",
  CREATED_WITH_WARNING: "Needs attention",
};

export function attemptStateOf(dest: DestinationAttemptLike): string {
  return String(
    dest.attempt_state || dest.creation_status || dest.status || ""
  ).trim();
}

export function destinationProgressLabel(dest: DestinationAttemptLike): string {
  const state = attemptStateOf(dest);
  if (PROGRESS_LABELS[state]) return PROGRESS_LABELS[state];
  if (!state) return "Preparing";
  return state.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

export function shouldShowRetry(dest: DestinationAttemptLike): boolean {
  const state = attemptStateOf(dest);
  if (state === "VERIFIED" || state === "ALREADY_EXISTS") return false;
  if (state === "NEEDS_RECONCILIATION") return false;
  if (state === "FAILED_SAFE_TO_RETRY") return Boolean(dest.attempt_id);
  if (["Failed", "FAILED", "CREATED_WITH_WARNING"].includes(state)) {
    return Boolean(dest.attempt_id) || Boolean(dest.retry_safe?.item_id);
  }
  return false;
}

export function shouldShowReconcile(dest: DestinationAttemptLike): boolean {
  const state = attemptStateOf(dest);
  return state === "NEEDS_RECONCILIATION" && Boolean(dest.attempt_id);
}

export function destinationActionKind(
  dest: DestinationAttemptLike
): "retry" | "reconcile" | "none" {
  if (shouldShowReconcile(dest)) return "reconcile";
  if (shouldShowRetry(dest)) return "retry";
  return "none";
}
