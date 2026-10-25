/** Per-store Add Product progress labels, recovery hints, and diagnostics helpers. */

export type DestinationAttemptLike = {
  status?: string | null;
  creation_status?: string | null;
  attempt_state?: string | null;
  attempt_id?: string | null;
  reason?: string | null;
  item_id?: string | null;
  retry_safe?: { item_id?: string; step?: string } | null;
  store?: {
    store_id?: string | null;
    display_name?: string | null;
  } | null;
};

export type StageHistoryEntry = {
  stage?: string;
  at_ms?: number;
  store_id?: string;
  status?: string;
  reason?: string;
  message?: string;
  [key: string]: unknown;
};

const PROGRESS_LABELS: Record<string, string> = {
  PREPARING: "Preparing",
  ANALYZING: "Analyzing",
  VALIDATING: "Validating",
  PREPARING_IMAGES: "Preparing images",
  CREATING: "Creating",
  CREATED_UNVERIFIED: "Verifying",
  VERIFYING: "Verifying",
  VERIFIED: "Succeeded",
  CREATED: "Succeeded",
  Active: "Succeeded",
  Created: "Succeeded",
  "Pending QC": "Succeeded",
  NEEDS_ATTENTION: "Needs attention",
  NEEDS_RECONCILIATION: "Needs attention",
  FAILED_SAFE_TO_RETRY: "Failed",
  FAILED: "Failed",
  Failed: "Failed",
  READY: "Ready",
  ALREADY_EXISTS: "Already on destination",
  POSSIBLE_DUPLICATE: "Needs attention",
  BLOCKED_CREATE: "Ready",
  CREATED_WITH_WARNING: "Needs attention",
  Checking: "Checking",
  Processing: "Processing",
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
  destination_store_not_found: "Destination store was not found in this workspace",
  destination_must_differ_from_source: "Choose a different store than the source",
};

const RECOVERY_HINTS: Record<string, string> = {
  missing_price:
    "Enter a regular price for each unresolved variant (or a single override when all SKUs share one price), then continue.",
  product_create_attempt_requires_reconciliation:
    "Open Reconcile for this store. Do not create again until the prior attempt is resolved.",
  create_claim_uncertain_requires_reconciliation:
    "Use Reconcile — a prior create may have partially succeeded.",
  stale_claim_requires_reconciliation:
    "Use Reconcile before retrying so you do not create a duplicate.",
  concurrent_create_claim_held:
    "Wait for the in-progress create to finish, then refresh or reconcile if needed.",
  already_copied_to_destination:
    "No action needed — the product is already on this destination.",
  product_create_attempt_already_verified:
    "No action needed — a verified listing already exists for this source.",
  category_unresolved:
    "Review category mapping or choose a different source product before creating.",
  category_evidence_conflict:
    "Resolve category conflict in the draft preview before creating.",
  destination_store_not_found:
    "Re-select the destination store and confirm it is still connected.",
  destination_must_differ_from_source:
    "Pick another destination store that is not the source.",
  missing_destination_item_id:
    "Use Reconcile to locate the listing before any retry.",
};

export type DestinationUiStatus =
  | "ready"
  | "checking"
  | "attention"
  | "processing"
  | "succeeded"
  | "failed"
  | "blocked";

export function attemptStateOf(dest: DestinationAttemptLike): string {
  return String(
    dest.attempt_state || dest.creation_status || dest.status || ""
  ).trim();
}

export function destinationProgressLabel(dest: DestinationAttemptLike): string {
  const state = attemptStateOf(dest);
  if (PROGRESS_LABELS[state]) return PROGRESS_LABELS[state];
  if (!state) return "Ready";
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
  if (raw.startsWith("destination_error:")) {
    return "Destination processing failed — see diagnostics for the safe error class";
  }
  if (raw.length > 160) return `${raw.slice(0, 157)}…`;
  return raw.replace(/_/g, " ");
}

export function destinationRecoveryHint(dest: DestinationAttemptLike): string {
  const raw = String(dest.reason || "").trim();
  if (raw && RECOVERY_HINTS[raw]) return RECOVERY_HINTS[raw];
  const state = attemptStateOf(dest);
  if (state === "NEEDS_RECONCILIATION") {
    return "Use Reconcile for this store. Do not submit Create again until resolved.";
  }
  if (state === "FAILED_SAFE_TO_RETRY" || state === "Failed" || state === "FAILED") {
    return "Review the reason, then use Retry only if it is marked safe to retry.";
  }
  if (raw.startsWith("CreateProduct:")) {
    return "Fix the reported Daraz issue if possible, then retry only when safe.";
  }
  if (!raw) return "Review validation details below. If unclear, copy diagnostics and share the ID.";
  return "Address the reason shown for this store before creating again.";
}

export function destinationUiStatus(
  dest: DestinationAttemptLike,
  opts?: { processing?: boolean; checking?: boolean }
): DestinationUiStatus {
  if (opts?.processing) return "processing";
  if (opts?.checking) return "checking";
  const state = attemptStateOf(dest);
  if (
    ["VERIFIED", "Created", "CREATED", "Active", "Pending QC", "ALREADY_EXISTS"].includes(
      state
    )
  ) {
    return state === "ALREADY_EXISTS" ? "succeeded" : "succeeded";
  }
  if (["FAILED_SAFE_TO_RETRY", "FAILED", "Failed"].includes(state)) return "failed";
  if (
    [
      "NEEDS_ATTENTION",
      "NEEDS_RECONCILIATION",
      "POSSIBLE_DUPLICATE",
      "CREATED_WITH_WARNING",
    ].includes(state)
  ) {
    return "attention";
  }
  if (["CREATING", "PREPARING", "CREATED_UNVERIFIED", "VERIFYING"].includes(state)) {
    return "processing";
  }
  if (state === "BLOCKED_CREATE" || state === "READY") return "ready";
  if (!state) return "ready";
  return "attention";
}

export function destinationStatusTone(
  status: DestinationUiStatus
): "ok" | "warn" | "danger" | "muted" {
  if (status === "succeeded" || status === "ready") return "ok";
  if (status === "attention" || status === "checking" || status === "processing") return "warn";
  if (status === "failed") return "danger";
  return "muted";
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

export type AttentionSummaryLine = {
  storeLabel: string;
  statusLabel: string;
  reasonLabel: string;
  recovery: string;
  attemptId?: string;
};

/** Build actionable per-store lines instead of a bare count. */
export function buildAttentionSummary(
  destinations: DestinationAttemptLike[] | null | undefined,
  opts?: { diagnosticId?: string | null }
): { headline: string; lines: AttentionSummaryLine[] } {
  const attention = (destinations || []).filter((d) => {
    const ui = destinationUiStatus(d);
    return ui === "attention" || ui === "failed";
  });
  const lines: AttentionSummaryLine[] = attention.map((d) => {
    const reasonRaw = String(d.reason || "").trim();
    const reasonLabel = reasonRaw
      ? destinationReasonLabel(reasonRaw)
      : opts?.diagnosticId
        ? `Reason unavailable · diagnostic ${opts.diagnosticId}`
        : "Reason unavailable";
    return {
      storeLabel: String(d.store?.display_name || d.store?.store_id || "Store"),
      statusLabel: destinationProgressLabel(d),
      reasonLabel,
      recovery: destinationRecoveryHint(d),
      attemptId: d.attempt_id || undefined,
    };
  });
  const headline =
    lines.length === 0
      ? ""
      : lines.length === 1
        ? `${lines[0].storeLabel}: ${lines[0].reasonLabel}`
        : `${lines.length} stores need attention — see per-store details`;
  return { headline, lines };
}

export type ClientStageId =
  | "validate"
  | "check_destination"
  | "check_attempts"
  | "prepare"
  | "create"
  | "confirm";

export type ClientStageState = "pending" | "active" | "done" | "skipped";

export function buildClientTimeline(opts: {
  phase: "idle" | "fetching" | "preparing" | "creating" | "done" | "error";
  hasResult: boolean;
  executedCreate: boolean;
}): Array<{ id: ClientStageId; label: string; state: ClientStageState }> {
  const { phase, hasResult, executedCreate } = opts;
  const steps: Array<{ id: ClientStageId; label: string }> = [
    { id: "validate", label: "Validating product" },
    { id: "check_destination", label: "Checking destination store" },
    { id: "check_attempts", label: "Checking existing or uncertain attempts" },
    { id: "prepare", label: "Preparing the product" },
    { id: "create", label: "Sending create request" },
    { id: "confirm", label: "Confirming the result" },
  ];

  const order: ClientStageId[] = steps.map((s) => s.id);
  let active: ClientStageId | null = null;
  if (phase === "fetching") active = "validate";
  else if (phase === "preparing") active = "prepare";
  else if (phase === "creating") active = "create";
  else if (phase === "done" || phase === "error") active = null;

  return steps.map((step) => {
    if (phase === "idle" && !hasResult) {
      return { ...step, state: "pending" as const };
    }
    // Never imply CreateProduct started unless execute was actually submitted.
    if (
      step.id === "create" &&
      !executedCreate &&
      phase !== "creating" &&
      (phase === "preparing" || phase === "fetching" || phase === "done" || hasResult)
    ) {
      return { ...step, state: "skipped" as const };
    }
    if (
      phase === "done" ||
      (hasResult &&
        phase !== "creating" &&
        phase !== "preparing" &&
        phase !== "fetching")
    ) {
      if (step.id === "create" && !executedCreate) {
        return { ...step, state: "skipped" as const };
      }
      return { ...step, state: "done" as const };
    }
    if (!active) return { ...step, state: "pending" as const };
    const ai = order.indexOf(active);
    const si = order.indexOf(step.id);
    if (si < ai) {
      if (step.id === "create" && !executedCreate) {
        return { ...step, state: "skipped" as const };
      }
      return { ...step, state: "done" as const };
    }
    if (si === ai) return { ...step, state: "active" as const };
    if (step.id === "create" && !executedCreate) {
      return { ...step, state: "skipped" as const };
    }
    return { ...step, state: "pending" as const };
  });
}

export function formatDiagnosticsCopy(input: {
  correlationId?: string | null;
  phase?: string;
  message?: string;
  destinations?: DestinationAttemptLike[];
  stageHistory?: StageHistoryEntry[];
  elapsedMs?: number | null;
}): string {
  const lines: string[] = [
    "Daraz Multi-Store — Add Product diagnostics (redacted)",
    `Generated: ${new Date().toISOString()}`,
  ];
  if (input.correlationId) lines.push(`Diagnostic ID: ${input.correlationId}`);
  if (input.phase) lines.push(`Client phase: ${input.phase}`);
  if (input.elapsedMs != null) lines.push(`Elapsed ms: ${Math.round(input.elapsedMs)}`);
  if (input.message) lines.push(`Message: ${input.message}`);
  lines.push("");
  lines.push("Per-store:");
  for (const d of input.destinations || []) {
    const reason = destinationReasonLabel(d.reason) || "Reason unavailable";
    lines.push(
      `- ${d.store?.display_name || d.store?.store_id || "Store"} | ${destinationProgressLabel(d)} | ${reason}` +
        (d.attempt_id ? ` | attempt ${d.attempt_id}` : "")
    );
  }
  if (input.stageHistory?.length) {
    lines.push("");
    lines.push("Stage history:");
    for (const s of input.stageHistory) {
      const bits = [
        s.stage,
        s.at_ms != null ? `+${s.at_ms}ms` : null,
        s.store_id ? `store=${s.store_id}` : null,
        s.status ? `status=${s.status}` : null,
        s.reason ? `reason=${destinationReasonLabel(String(s.reason))}` : null,
      ].filter(Boolean);
      lines.push(`- ${bits.join(" · ")}`);
    }
  }
  lines.push("");
  lines.push("No tokens, cookies, or secrets included.");
  return lines.join("\n");
}

export function extractDiagnosticId(err: unknown): string | null {
  if (!err || typeof err !== "object") return null;
  const root = err as { detail?: unknown; diagnostic_id?: unknown };
  if (typeof root.diagnostic_id === "string" && root.diagnostic_id.trim()) {
    return root.diagnostic_id.trim();
  }
  const detail = root.detail;
  if (detail && typeof detail === "object") {
    const obj = detail as { diagnostic_id?: unknown; detail?: unknown };
    if (typeof obj.diagnostic_id === "string" && obj.diagnostic_id.trim()) {
      return obj.diagnostic_id.trim();
    }
    // FastAPI body shape: { detail: { message, diagnostic_id } } stored on ApiError.detail
    if (obj.detail && typeof obj.detail === "object") {
      const inner = obj.detail as { diagnostic_id?: unknown };
      if (typeof inner.diagnostic_id === "string" && inner.diagnostic_id.trim()) {
        return inner.diagnostic_id.trim();
      }
    }
  }
  return null;
}
