/** Read-only No Brand diagnostic helpers — UI never invents category or brand IDs. */

export type NoBrandLookup = {
  strategy?: string | null;
  pages?: number | null;
  name_filter_effective?: boolean | null;
  complete?: boolean | null;
  stop_reason?: string | null;
  total_record?: number | null;
  ordering?: string | null;
  matched_start_row?: number | null;
  name_filter_ignored_for?: string | null;
  matched_query?: string | null;
  cache_hit?: boolean | null;
  scan_from_start_row?: number | null;
  scanned_rows?: number | null;
  next_start_row?: number | null;
  verified_at?: string | null;
  verify_uniqueness?: boolean | null;
  uniqueness_verified?: boolean | null;
  candidates_total?: number | null;
};

export type NoBrandCandidate = {
  brand?: string | null;
  brand_id?: number | string | null;
  matched_start_row?: number | null;
};

export type CatalogBrandCount = { brand: string; count: number };

export type CatalogBrandScopeEvidence = {
  store_id?: string | null;
  product_count?: number;
  category_product_count?: number;
  top_brands_overall?: CatalogBrandCount[];
  top_brands_in_category?: CatalogBrandCount[];
  no_brand_sample_item_ids?: string[];
};

export type CatalogBrandEvidence = {
  status?: string | null;
  message?: string | null;
  primary_category_id?: string | null;
  workspace?: CatalogBrandScopeEvidence | null;
  destination_store?: CatalogBrandScopeEvidence | null;
};

export type NoBrandCheckResult = {
  status: string;
  reason?: string | null;
  brand?: string | null;
  brand_id?: number | string | null;
  lookup_calls?: number | null;
  primary_category_id?: string | null;
  store_id?: string | null;
  lookup?: NoBrandLookup | null;
  catalog_evidence?: CatalogBrandEvidence | null;
  catalog_consistency_note?: string | null;
  deep?: boolean | null;
  complete?: boolean | null;
  next_start_row?: number | null;
  scanned_rows?: number | null;
  total_record?: number | null;
  candidates?: NoBrandCandidate[] | null;
  candidates_total?: number | null;
  persisted?: boolean | null;
  uniqueness_verified?: boolean | null;
  verify_uniqueness?: boolean | null;
  seller_item_limit?: Record<string, unknown> | null;
  image_preflight?: Record<string, unknown> | null;
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

/** Green only for uniqueness-verified NO_BRAND. */
export function noBrandCheckTone(
  status: string | null | undefined,
  uniquenessVerified?: boolean | null
): "ok" | "warn" | "muted" {
  if (!status) return "muted";
  if (status === "NO_BRAND" && uniquenessVerified) return "ok";
  if (status === "NO_BRAND") return "warn";
  return "warn";
}

export function isNoBrandConfirmed(
  result: NoBrandCheckResult | null | undefined
): boolean {
  return result?.status === "NO_BRAND" && Boolean(result.uniqueness_verified);
}

/** Blocks create unless uniqueness-verified NO_BRAND (or no check yet). */
export function brandCheckBlocksCreate(
  results: Record<string, NoBrandCheckResult | undefined>,
  storeIds: string[]
): boolean {
  for (const id of storeIds) {
    const r = results[id];
    if (r && !isNoBrandConfirmed(r)) return true;
  }
  return false;
}

export function canVerifyUniqueness(
  result: NoBrandCheckResult | null | undefined
): boolean {
  if (!result) return false;
  if (result.uniqueness_verified) return false;
  if (result.brand_id == null || result.brand_id === "") return false;
  if (result.complete === true && result.status === "NO_BRAND") return false;
  return (
    result.status === "NO_BRAND" ||
    (result.status === "UNVERIFIED" && Boolean(result.brand_id))
  );
}

function formatBrandCounts(rows: CatalogBrandCount[] | undefined, label: string): string | null {
  if (!rows?.length) return null;
  const bits = rows
    .slice(0, 5)
    .map((r) => `${r.brand}=${r.count}`)
    .join(", ");
  return `${label}: ${bits}`;
}

export function formatNoBrandCheckLine(result: NoBrandCheckResult): string {
  const lookup = result.lookup || {};
  const bits = [
    `status=${result.status}`,
    result.reason ? `reason=${result.reason}` : null,
    result.brand != null && result.brand !== "" ? `brand=${result.brand}` : null,
    result.brand_id != null && result.brand_id !== ""
      ? `brand_id=${result.brand_id}`
      : null,
    result.lookup_calls != null ? `lookup_calls=${result.lookup_calls}` : null,
    lookup.pages != null ? `pages=${lookup.pages}` : null,
    lookup.stop_reason ? `stop_reason=${lookup.stop_reason}` : null,
    lookup.strategy ? `strategy=${lookup.strategy}` : null,
    lookup.name_filter_effective != null
      ? `name_filter_effective=${lookup.name_filter_effective}`
      : null,
    lookup.ordering ? `ordering=${lookup.ordering}` : null,
    lookup.matched_start_row != null
      ? `matched_start_row=${lookup.matched_start_row}`
      : null,
    lookup.total_record != null ? `total_record=${lookup.total_record}` : null,
    result.deep ? "deep=true" : null,
    result.complete != null ? `complete=${result.complete}` : null,
    result.scanned_rows != null ? `scanned_rows=${result.scanned_rows}` : null,
    result.next_start_row != null ? `next_start_row=${result.next_start_row}` : null,
    result.persisted ? "persisted=true" : null,
    result.uniqueness_verified != null
      ? `uniqueness_verified=${result.uniqueness_verified}`
      : null,
    result.candidates_total != null
      ? `candidates_total=${result.candidates_total}`
      : result.candidates?.length
        ? `candidates=${result.candidates.length}`
        : null,
    result.error ? `error=${result.error}` : null,
  ].filter(Boolean);
  return bits.join(" · ");
}

export function deepScanProgressLabel(result: NoBrandCheckResult | null | undefined): string {
  if (!result) return "";
  const total = result.total_record ?? result.lookup?.total_record;
  const scanned = result.scanned_rows ?? result.lookup?.scanned_rows;
  const next = result.next_start_row ?? result.lookup?.next_start_row;
  const bits = [
    scanned != null ? `scanned_rows=${scanned}` : null,
    total != null ? `total_record=${total}` : null,
    next != null ? `next_start_row=${next}` : null,
    result.complete != null ? `complete=${result.complete}` : null,
    result.lookup?.stop_reason ? `stop=${result.lookup.stop_reason}` : null,
  ].filter(Boolean);
  return bits.join(" · ");
}

export function canContinueDeepScan(result: NoBrandCheckResult | null | undefined): boolean {
  if (!result || result.status === "NO_BRAND") return false;
  if (result.complete === true) return false;
  if (result.lookup?.stop_reason === "rate_limited") return false;
  return result.next_start_row != null || result.lookup?.next_start_row != null;
}

export function formatCatalogEvidenceLines(
  evidence: CatalogBrandEvidence | null | undefined
): string[] {
  if (!evidence) return [];
  const lines: string[] = [
    `catalog_evidence.status=${evidence.status || "unknown"}`,
  ];
  if (evidence.message) lines.push(`catalog_evidence.message=${evidence.message}`);
  const ws = evidence.workspace;
  const dest = evidence.destination_store;
  if (ws) {
    lines.push(
      `workspace products=${ws.product_count ?? 0} in_category=${ws.category_product_count ?? 0}`
    );
    const topCat = formatBrandCounts(ws.top_brands_in_category, "workspace top brands (category)");
    if (topCat) lines.push(topCat);
    if (ws.no_brand_sample_item_ids?.length) {
      lines.push(
        `workspace no_brand sample item_ids=${ws.no_brand_sample_item_ids.join(",")}`
      );
    }
  }
  if (dest) {
    lines.push(
      `destination_store products=${dest.product_count ?? 0} in_category=${dest.category_product_count ?? 0}`
    );
    const topCat = formatBrandCounts(
      dest.top_brands_in_category,
      "destination top brands (category)"
    );
    if (topCat) lines.push(topCat);
    if (dest.no_brand_sample_item_ids?.length) {
      lines.push(
        `destination no_brand sample item_ids=${dest.no_brand_sample_item_ids.join(",")}`
      );
    }
  }
  return lines;
}

export function mapApiNoBrandResult(
  res: Record<string, unknown>,
  storeId: string,
  categoryId: string
): NoBrandCheckResult {
  const lookupRaw = (res.lookup as Record<string, unknown> | undefined) || {};
  const lookup: NoBrandLookup = {
    strategy: (lookupRaw.strategy as string | undefined) ?? null,
    pages: (lookupRaw.pages as number | undefined) ?? null,
    name_filter_effective:
      (lookupRaw.name_filter_effective as boolean | undefined) ?? null,
    complete: (lookupRaw.complete as boolean | undefined) ?? null,
    stop_reason: (lookupRaw.stop_reason as string | undefined) ?? null,
    total_record: (lookupRaw.total_record as number | undefined) ?? null,
    ordering: (lookupRaw.ordering as string | undefined) ?? null,
    matched_start_row: (lookupRaw.matched_start_row as number | undefined) ?? null,
    name_filter_ignored_for:
      (lookupRaw.name_filter_ignored_for as string | undefined) ?? null,
    matched_query: (lookupRaw.matched_query as string | undefined) ?? null,
    cache_hit: (lookupRaw.cache_hit as boolean | undefined) ?? null,
    scan_from_start_row:
      (lookupRaw.scan_from_start_row as number | undefined) ?? null,
    scanned_rows: (lookupRaw.scanned_rows as number | undefined) ?? null,
    next_start_row: (lookupRaw.next_start_row as number | undefined) ?? null,
    verified_at: (lookupRaw.verified_at as string | undefined) ?? null,
  };
  return {
    status: String(res.status || "UNVERIFIED"),
    reason: (res.reason as string | null | undefined) ?? null,
    brand: (res.brand as string | null | undefined) ?? null,
    brand_id: (res.brand_id as string | number | null | undefined) ?? null,
    lookup_calls: (res.lookup_calls as number | null | undefined) ?? null,
    primary_category_id:
      (res.primary_category_id as string | null | undefined) ?? categoryId,
    store_id: (res.store_id as string | null | undefined) ?? storeId,
    lookup,
    catalog_evidence:
      (res.catalog_evidence as CatalogBrandEvidence | null | undefined) ?? null,
    catalog_consistency_note:
      (res.catalog_consistency_note as string | null | undefined) ?? null,
    deep: (res.deep as boolean | null | undefined) ?? null,
    complete:
      (res.complete as boolean | null | undefined) ??
      (lookup.complete as boolean | null | undefined) ??
      null,
    next_start_row:
      (res.next_start_row as number | null | undefined) ??
      lookup.next_start_row ??
      null,
    scanned_rows:
      (res.scanned_rows as number | null | undefined) ?? lookup.scanned_rows ?? null,
    total_record:
      (res.total_record as number | null | undefined) ?? lookup.total_record ?? null,
    candidates: (res.candidates as NoBrandCandidate[] | null | undefined) ?? null,
    candidates_total:
      (res.candidates_total as number | null | undefined) ??
      (lookup as { candidates_total?: number }).candidates_total ??
      null,
    persisted: (res.persisted as boolean | null | undefined) ?? null,
    uniqueness_verified:
      (res.uniqueness_verified as boolean | null | undefined) ??
      (lookup as { uniqueness_verified?: boolean }).uniqueness_verified ??
      null,
    verify_uniqueness:
      (res.verify_uniqueness as boolean | null | undefined) ?? null,
    seller_item_limit:
      (res.seller_item_limit as Record<string, unknown> | null | undefined) ?? null,
    image_preflight:
      (res.image_preflight as Record<string, unknown> | null | undefined) ?? null,
  };
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
