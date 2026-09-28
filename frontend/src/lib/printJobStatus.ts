/** Print job status helpers (interrupted / recovery / progress). */

export type PrintJobLike = {
  status?: string | null;
  message?: string | null;
  error?: string | null;
  processing_stage?: string | null;
  progress?: {
    completed?: number | null;
    total?: number | null;
    failed?: number | null;
  } | null;
  result?: Record<string, unknown> | null;
  failed_count?: number | null;
  summary?: {
    failed_count?: number | null;
    success_count?: number | null;
    selected_count?: number | null;
    retryable_order_ids?: string[] | null;
    failed_order_ids?: string[] | null;
  } | null;
  retryable_order_ids?: string[] | null;
  failed_order_ids?: string[] | null;
};

const STAGE_LABELS: Record<string, string> = {
  PREPARING: "Preparing",
  FETCHING: "Fetching labels",
  FETCHING_LABELS: "Fetching labels",
  PROCESSING: "Processing",
  MERGING: "Merging PDF",
  MERGING_PDF: "Merging PDF",
  SAVING: "Saving history",
  SAVING_HISTORY: "Saving history",
  COMPLETE: "Complete",
  COMPLETED: "Complete",
  RECOVERY_REQUIRED: "Interrupted — recovery required",
};

export function isPrintJobInterrupted(job: PrintJobLike | null | undefined): boolean {
  if (!job) return false;
  const status = String(job.status || "").toLowerCase();
  const stage = String(job.processing_stage || "");
  return status === "interrupted" || stage === "RECOVERY_REQUIRED";
}

export function printJobStageLabel(job: PrintJobLike | null | undefined): string {
  if (!job) return "";
  if (isPrintJobInterrupted(job)) return "Interrupted — recovery required";
  const stage = String(job.processing_stage || "").trim();
  if (STAGE_LABELS[stage]) return STAGE_LABELS[stage];
  const status = String(job.status || "").toLowerCase();
  if (status === "completed" || status === "done") return "Complete";
  if (status === "running" || status === "processing") {
    return STAGE_LABELS[stage] || job.message || "Processing";
  }
  return String(job.message || stage || "").trim();
}

export function printJobProgressText(job: PrintJobLike | null | undefined): string {
  if (!job) return "";
  const p = job.progress || {};
  const completed = Number(p.completed ?? 0);
  const total = Number(p.total ?? 0);
  const failed =
    Number(p.failed ?? job.failed_count ?? job.summary?.failed_count ?? 0) || 0;
  if (total > 0) {
    return `${completed} / ${total} completed` + (failed ? ` · ${failed} failed` : "");
  }
  if (failed) return `${failed} failed`;
  return "";
}

/** Prefer retryable_order_ids (excludes proven SUCCESS / ALREADY_PRINTED). */
export function retryablePrintOrderIds(job: PrintJobLike | null | undefined): string[] {
  if (!job) return [];
  const fromRoot = job.retryable_order_ids;
  const fromSummary = job.summary?.retryable_order_ids;
  const fromResult = (job.result as PrintJobLike | undefined)?.retryable_order_ids;
  const fromResultSummary = (job.result as PrintJobLike | undefined)?.summary
    ?.retryable_order_ids;
  const preferred =
    fromRoot || fromSummary || fromResult || fromResultSummary || null;
  if (Array.isArray(preferred) && preferred.length) {
    return preferred.map(String);
  }
  const fallback =
    job.failed_order_ids ||
    job.summary?.failed_order_ids ||
    (job.result as PrintJobLike | undefined)?.failed_order_ids ||
    [];
  return Array.isArray(fallback) ? fallback.map(String) : [];
}
