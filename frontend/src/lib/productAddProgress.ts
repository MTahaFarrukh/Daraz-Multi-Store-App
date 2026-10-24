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
  ANALYZING: "Analyzing",
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
  ALREADY_EXISTS: "Already on destination",
  POSSIBLE_DUPLICATE: "Needs attention",
  BLOCKED_CREATE: "Validating",
  CREATED_WITH_WARNING: "Needs attention",
};

const REASON_LABELS: Record<string, string> = {
  already_copied_to_destination:
    "Already copied to this store — skipped create",
  product_create_attempt_already_verified: "Already verified on this store",
  product_create_attempt_requires_reconciliation:
    "A prior create needs reconciliation before retrying",
  concurrent_create_claim_held:
    "Another create is in progress for this product — wait and reconcile if needed",
  create_claim_uncertain_requires_reconciliation:
    "A prior create outcome is uncertain — reconcile before retrying",
  stale_claim_requires_reconciliation:
    "A previous attempt may have created the product — reconcile before retrying",
  missing_price: "Enter prices for unresolved variants",
  missing_destination_item_id:
    "Create finished without a destination item ID — reconcile before retrying",
  category_unresolved: "Category could not be resolved for the destination",
  category_evidence_conflict: "Category evidence conflicts — review before creating",
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

/** Human-readable reason; never expose raw stack traces or tokens. */
export function destinationReasonLabel(reason: string | null | undefined): string {
  const raw = String(reason || "").trim();
  if (!raw) return "";
  if (REASON_LABELS[raw]) return REASON_LABELS[raw];
  if (raw.startsWith("CreateProduct:")) {
    return "Daraz rejected the create request — see details and retry if safe";
  }
  if (raw.length > 160) return `${raw.slice(0, 157)}…`;
  return raw.replace(/_/g, " ");
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
