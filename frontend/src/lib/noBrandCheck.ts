/** Read-only No Brand diagnostic helpers — UI never invents category or brand IDs. */

export type NoBrandCheckResult = {
  status: string;
  reason?: string | null;
  brand?: string | null;
  brand_id?: number | string | null;
  lookup_calls?: number | null;
  primary_category_id?: string | null;
  store_id?: string | null;
  /** Client-side message (e.g. 403); never includes tokens. */
  error?: string | null;
  http_status?: number | null;
};

/** Exact primary_category_id from preview draft only — never guess. */
export function extractPreviewPrimaryCategoryId(
  draftPreview: Record<string, unknown> | null | undefined
): string | null {
  if (!draftPreview) return null;
  const draft = draftPreview.draft as Record<string, unknown> | undefined;
  const product = (draft?.product || draftPreview.product) as
    | Record<string, unknown>
    | undefined;
  const raw =
    product?.primary_category_id ??
    draft?.primary_category_id ??
    draftPreview.primary_category_id;
  if (raw == null || raw === "") return null;
  const s = String(raw).trim();
  return s || null;
}

export function canCheckNoBrand(categoryId: string | null | undefined): boolean {
  return Boolean(categoryId && String(categoryId).trim());
}

/** Green only for confirmed NO_BRAND. */
export function noBrandCheckTone(status: string | null | undefined): "ok" | "warn" | "muted" {
  if (!status) return "muted";
  return status === "NO_BRAND" ? "ok" : "warn";
}

export function isNoBrandConfirmed(
  result: NoBrandCheckResult | null | undefined
): boolean {
  return result?.status === "NO_BRAND";
}

/** Any non-NO_BRAND result for a selected store blocks create. */
export function brandCheckBlocksCreate(
  results: Record<string, NoBrandCheckResult | undefined>,
  storeIds: string[]
): boolean {
  for (const id of storeIds) {
    const r = results[id];
    if (r && r.status !== "NO_BRAND") return true;
  }
  return false;
}

export function formatNoBrandCheckLine(result: NoBrandCheckResult): string {
  const bits = [
    `status=${result.status}`,
    result.reason ? `reason=${result.reason}` : null,
    result.brand != null && result.brand !== "" ? `brand=${result.brand}` : null,
    result.brand_id != null && result.brand_id !== ""
      ? `brand_id=${result.brand_id}`
      : null,
    result.lookup_calls != null ? `lookup_calls=${result.lookup_calls}` : null,
    result.error ? `error=${result.error}` : null,
  ].filter(Boolean);
  return bits.join(" · ");
}

export function noBrandCheckFromHttpError(
  err: unknown,
  storeId: string,
  categoryId: string
): NoBrandCheckResult {
  const status =
    err && typeof err === "object" && "status" in err
      ? Number((err as { status?: number }).status)
      : 0;
  const message =
    err instanceof Error ? err.message : typeof err === "string" ? err : "Request failed";
  if (status === 403) {
    return {
      status: "UNVERIFIED",
      reason: "permission_denied",
      brand: null,
      brand_id: null,
      lookup_calls: null,
      primary_category_id: categoryId,
      store_id: storeId,
      error: "Permission denied (403) — product.create capability required",
      http_status: 403,
    };
  }
  return {
    status: "UNVERIFIED",
    reason: "request_failed",
    brand: null,
    brand_id: null,
    lookup_calls: null,
    primary_category_id: categoryId,
    store_id: storeId,
    error: message.slice(0, 200),
    http_status: status || null,
  };
}
