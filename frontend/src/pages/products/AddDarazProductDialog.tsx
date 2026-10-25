import { useEffect, useMemo, useRef, useState } from "react";
import {
  useAddProductFromConnected,
  useAddProductFromUrl,
  useImportUrlDraft,
} from "@/hooks/queries/useProducts";
import { useProductCreateEnabled } from "@/hooks/useProductCreateEnabled";
import { Api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import {
  brandCheckBlocksCreate,
  canCheckNoBrand,
  extractPreviewPrimaryCategoryId,
  noBrandCheckFromHttpError,
  type NoBrandCheckResult,
} from "@/lib/noBrandCheck";
import {
  buildAttentionSummary,
  extractDiagnosticId,
  type StageHistoryEntry,
} from "@/lib/productAddProgress";
import type { AddProductDestinationResult, AddProductResponse, StoreView } from "@/types/api";
import { CloneDraftPreview } from "@/pages/products/CloneDraftPreview";
import { Dialog } from "@/components/ui/Dialog";
import { StatusBadge } from "@/components/ui/Primitives";
import { DestinationStatusCard } from "@/pages/products/addProduct/DestinationStatusCard";
import { DiagnosticsPanel } from "@/pages/products/addProduct/DiagnosticsPanel";
import { ProcessingTimeline } from "@/pages/products/addProduct/ProcessingTimeline";

type SourceMode = "url" | "connected";
type Phase = "idle" | "fetching" | "preparing" | "creating" | "done" | "error";

type Props = {
  open: boolean;
  onClose: () => void;
  stores: StoreView[];
  workspaceId?: string;
  onError: (msg: string) => void;
  onOk: (msg: string) => void;
  busy: string;
  setBusy: (msg: string) => void;
};

export function AddDarazProductDialog({
  open,
  onClose,
  stores,
  workspaceId,
  onError,
  onOk,
  busy,
  setBusy,
}: Props) {
  const [mode, setMode] = useState<SourceMode>("url");
  const [productUrl, setProductUrl] = useState("");
  const [sourceStore, setSourceStore] = useState("");
  const [itemId, setItemId] = useState("");
  const [destSelected, setDestSelected] = useState<Set<string>>(new Set());
  const [editBefore, setEditBefore] = useState(false);
  const [priceOverride, setPriceOverride] = useState("");
  const [variantPrices, setVariantPrices] = useState<Record<string, string>>({});
  const [result, setResult] = useState<AddProductResponse | null>(null);
  const [draftPreview, setDraftPreview] = useState<Record<string, unknown> | null>(null);
  const [fetchedUrl, setFetchedUrl] = useState<string | null>(null);
  const [extractionComplete, setExtractionComplete] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [localAlert, setLocalAlert] = useState<{ tone: "ok" | "warn" | "error"; text: string } | null>(
    null
  );
  const [correlationId, setCorrelationId] = useState<string | null>(null);
  const [stageHistory, setStageHistory] = useState<StageHistoryEntry[]>([]);
  const [executedCreate, setExecutedCreate] = useState(false);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  /** Synchronous lock — closes React double-click TOCTOU before setSubmitting re-renders. */
  const inFlightRef = useRef(false);
  /** Separate lock for read-only No Brand GET (independent of create gate). */
  const brandCheckInFlightRef = useRef(false);
  const [noBrandByStore, setNoBrandByStore] = useState<
    Record<string, NoBrandCheckResult | undefined>
  >({});
  const [brandCheckingStoreId, setBrandCheckingStoreId] = useState<string | null>(null);

  const addUrlMutation = useAddProductFromUrl(workspaceId);
  const addConnectedMutation = useAddProductFromConnected(workspaceId);
  const importDraftMutation = useImportUrlDraft(workspaceId);
  const productCreateEnabled = useProductCreateEnabled();
  const createGateOn = productCreateEnabled === true;

  const destOptions = useMemo(
    () => stores.filter((s) => (mode === "connected" ? s.store_id !== sourceStore : true)),
    [stores, sourceStore, mode]
  );

  const primaryDest = useMemo(() => {
    const first = Array.from(destSelected)[0];
    return first || "";
  }, [destSelected]);

  const selectedStores = useMemo(
    () => destOptions.filter((s) => destSelected.has(s.store_id)),
    [destOptions, destSelected]
  );

  const unresolvedVariants = useMemo(() => {
    const seen = new Map<string, { key: string; label: string }>();
    for (const d of result?.destinations || []) {
      for (const v of d.unresolved_variants || []) {
        const key = String(v.key || "");
        if (!key || seen.has(key)) continue;
        seen.set(key, { key, label: String(v.label || key) });
      }
    }
    return Array.from(seen.values());
  }, [result]);

  const urlFetched =
    mode === "url" &&
    Boolean(fetchedUrl) &&
    fetchedUrl === productUrl.trim() &&
    Boolean(draftPreview);

  const previewCategoryId = useMemo(
    () => extractPreviewPrimaryCategoryId(draftPreview),
    [draftPreview]
  );
  const brandBlocksCreate = brandCheckBlocksCreate(
    noBrandByStore,
    selectedStores.map((s) => s.store_id)
  );

  const canPrepare =
    !busy &&
    !submitting &&
    destSelected.size > 0 &&
    (mode === "connected"
      ? Boolean(sourceStore && itemId.trim())
      : urlFetched && Boolean(productUrl.trim()));
  const canCreate = canPrepare && createGateOn && !brandBlocksCreate;

  const draftVariants =
    ((draftPreview?.draft as Record<string, unknown> | undefined)?.variants as Array<
      Record<string, unknown>
    >) || [];

  const productTitle =
    String(
      (draftPreview?.draft as { product?: { title?: string } } | undefined)?.product?.title ||
        (result?.source as { title?: string } | undefined)?.title ||
        ""
    ) || null;

  const thumb =
    (
      (
        (draftPreview?.draft as { media?: { product_images?: string[] } } | undefined)?.media
          ?.product_images || []
      )[0] as string | undefined
    ) || null;

  const attention = useMemo(
    () => buildAttentionSummary(result?.destinations, { diagnosticId: correlationId }),
    [result, correlationId]
  );

  useEffect(() => {
    if (!submitting || !startedAtRef.current) return;
    const id = window.setInterval(() => {
      if (startedAtRef.current) {
        setElapsedMs(Date.now() - startedAtRef.current);
      }
    }, 250);
    return () => window.clearInterval(id);
  }, [submitting]);

  function beginWork(next: Phase) {
    startedAtRef.current = Date.now();
    setElapsedMs(0);
    setPhase(next);
  }

  function endWork(next: Phase) {
    if (startedAtRef.current) {
      setElapsedMs(Date.now() - startedAtRef.current);
    }
    setPhase(next);
  }

  function toggleDest(id: string) {
    setDestSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllDest() {
    setDestSelected(new Set(destOptions.map((s) => s.store_id)));
  }

  function clearDest() {
    setDestSelected(new Set());
  }

  function resetAndClose() {
    if (submitting) return;
    setMode("url");
    setProductUrl("");
    setSourceStore("");
    setItemId("");
    setDestSelected(new Set());
    setEditBefore(false);
    setPriceOverride("");
    setVariantPrices({});
    setResult(null);
    setDraftPreview(null);
    setFetchedUrl(null);
    setExtractionComplete(null);
    setSubmitting(false);
    setPhase("idle");
    setLocalAlert(null);
    setCorrelationId(null);
    setStageHistory([]);
    setExecutedCreate(false);
    setElapsedMs(null);
    startedAtRef.current = null;
    setNoBrandByStore({});
    setBrandCheckingStoreId(null);
    brandCheckInFlightRef.current = false;
    onClose();
  }

  async function onCheckNoBrand(storeId: string) {
    if (brandCheckInFlightRef.current || submitting) return;
    const categoryId = previewCategoryId;
    if (!canCheckNoBrand(categoryId) || !categoryId) {
      setAlerts(
        "warn",
        "Check No Brand needs the preview primary_category_id — fetch/preview first. Category id is never guessed."
      );
      return;
    }
    brandCheckInFlightRef.current = true;
    setBrandCheckingStoreId(storeId);
    try {
      const res = await Api.checkCategoryNoBrand(storeId, categoryId);
      const mapped: NoBrandCheckResult = {
        status: String(res.status || "UNVERIFIED"),
        reason: res.reason ?? null,
        brand: res.brand ?? null,
        brand_id: res.brand_id ?? null,
        lookup_calls: res.lookup_calls ?? null,
        primary_category_id: res.primary_category_id ?? categoryId,
        store_id: res.store_id ?? storeId,
      };
      setNoBrandByStore((prev) => ({ ...prev, [storeId]: mapped }));
      if (mapped.status === "NO_BRAND") {
        setAlerts("ok", `No Brand confirmed for ${storeId} (read-only).`, false);
      } else {
        setAlerts(
          "warn",
          `No Brand ${mapped.status} for ${storeId} — create stays blocked until confirmed.`,
          false
        );
      }
    } catch (err) {
      const mapped = noBrandCheckFromHttpError(err, storeId, categoryId);
      setNoBrandByStore((prev) => ({ ...prev, [storeId]: mapped }));
      setAlerts(
        "error",
        mapped.error ||
          (err instanceof ApiError ? err.message : "No Brand check failed"),
        false
      );
    } finally {
      brandCheckInFlightRef.current = false;
      setBrandCheckingStoreId(null);
    }
  }

  function parsePrice(): number | undefined {
    const raw = priceOverride.trim();
    if (!raw) return undefined;
    const n = Number(raw);
    if (!Number.isFinite(n) || n < 0) return undefined;
    return n;
  }

  function parseVariantOverrides(): Record<string, number> | undefined {
    const out: Record<string, number> = {};
    for (const [key, raw] of Object.entries(variantPrices)) {
      const t = raw.trim();
      if (!t) continue;
      const n = Number(t);
      if (!Number.isFinite(n) || n <= 0) continue;
      out[key] = n;
    }
    return Object.keys(out).length ? out : undefined;
  }

  function ingestResultMeta(res: AddProductResponse) {
    if (res.correlation_id) setCorrelationId(String(res.correlation_id));
    if (Array.isArray(res.stage_history)) {
      setStageHistory(res.stage_history as StageHistoryEntry[]);
    }
  }

  function setAlerts(tone: "ok" | "warn" | "error", text: string, alsoPage = true) {
    setLocalAlert({ tone, text });
    if (!alsoPage) return;
    if (tone === "ok") {
      onOk(text);
      onError("");
    } else if (tone === "error") {
      onError(text);
      onOk("");
    } else {
      onError(text);
      onOk("");
    }
  }

  async function onFetchProduct() {
    if (mode !== "url" || submitting) return;
    const url = productUrl.trim();
    if (!url) {
      setAlerts("error", "Paste a Daraz product URL first");
      return;
    }
    if (!primaryDest) {
      setAlerts("error", "Select at least one destination store before fetching");
      return;
    }
    setLocalAlert(null);
    onError("");
    onOk("");
    setResult(null);
    beginWork("fetching");
    setBusy("Fetching product…");
    setSubmitting(true);
    try {
      const res = await importDraftMutation.mutateAsync({
        url,
        destination_store_id: primaryDest,
      });
      setDraftPreview(res as unknown as Record<string, unknown>);
      setNoBrandByStore({});
      setFetchedUrl(url);
      const extracted = res as {
        extraction_complete?: boolean;
        draft?: { validation?: { warnings?: string[] } };
      };
      const complete =
        typeof extracted.extraction_complete === "boolean"
          ? extracted.extraction_complete
          : !(extracted.draft?.validation?.warnings || []).some((w) =>
              String(w).toLowerCase().includes("incomplete")
            );
      setExtractionComplete(complete);
      const warnCount = (res.warnings || []).length + (res.errors || []).length;
      if (complete && !warnCount) {
        setAlerts("ok", "Preview ready — review details, then Add Product.");
      } else if (complete) {
        setAlerts("warn", "Preview ready with notes — review warnings before adding.");
      } else {
        setAlerts(
          "error",
          "Extraction incomplete — missing fields are shown below. Fix what you can before adding."
        );
      }
      endWork("done");
    } catch (err) {
      setDraftPreview(null);
      setFetchedUrl(null);
      setExtractionComplete(null);
      const diag = err instanceof ApiError ? extractDiagnosticId(err) : null;
      if (diag) setCorrelationId(diag);
      setAlerts("error", err instanceof Error ? err.message : "Could not fetch product");
      endWork("error");
    } finally {
      setBusy("");
      setSubmitting(false);
    }
  }

  async function runAdd(options?: {
    destIds?: string[];
    execute?: boolean;
    confirm?: boolean;
    edit?: boolean;
    resumeByStore?: Record<string, string>;
  }) {
    if (submitting || inFlightRef.current) return;
    const destIds = options?.destIds ?? Array.from(destSelected);
    if (!destIds.length) {
      setAlerts("error", "Select at least one destination store");
      return;
    }
    if (mode === "url" && !urlFetched) {
      setAlerts("error", "Fetch the product first so you can review the preview");
      return;
    }
    const wantEdit = options?.edit ?? false;
    if (!wantEdit && !editBefore && !createGateOn) {
      setAlerts(
        "error",
        "Product creation is disabled on this server. You can Fetch and preview, but Add Product stays blocked until ALLOW_PRODUCT_CREATE is enabled after supervised proof."
      );
      return;
    }
    setLocalAlert(null);
    onError("");
    onOk("");
    setResult(null);
    const price = parsePrice();
    const variantOverrides = parseVariantOverrides();

    if (mode === "url" && !productUrl.trim()) {
      setAlerts("error", "Product URL is required");
      return;
    }
    if (mode === "connected" && (!sourceStore || !itemId.trim())) {
      setAlerts("error", "Source store and Item ID are required");
      return;
    }

    async function callApi(body: {
      execute: boolean;
      confirm: boolean;
      edit_before: boolean;
      destination_store_ids: string[];
      resume_by_store?: Record<string, string>;
    }) {
      if (mode === "url") {
        return addUrlMutation.mutateAsync({
          url: productUrl.trim(),
          destination_store_ids: body.destination_store_ids,
          price_override: price,
          variant_price_overrides: variantOverrides,
          execute: body.execute,
          confirm: body.confirm,
          edit_before: body.edit_before,
          resume_by_store: body.resume_by_store,
        });
      }
      return addConnectedMutation.mutateAsync({
        source_store_id: sourceStore,
        daraz_item_id: itemId.trim(),
        destination_store_ids: body.destination_store_ids,
        price_override: price,
        variant_price_overrides: variantOverrides,
        execute: body.execute,
        confirm: body.confirm,
        edit_before: body.edit_before,
        resume_by_store: body.resume_by_store,
      });
    }

    inFlightRef.current = true;
    setSubmitting(true);
    try {
      if (wantEdit || editBefore) {
        beginWork("preparing");
        setBusy("Preparing draft for edit…");
        setExecutedCreate(false);
        const res = await callApi({
          execute: false,
          confirm: false,
          edit_before: true,
          destination_store_ids: destIds,
        });
        setResult(res);
        ingestResultMeta(res);
        if (res.draft_result) {
          setDraftPreview(res.draft_result as Record<string, unknown>);
          setNoBrandByStore({});
        }
        setAlerts("ok", "Draft ready — review below, then Add Product to create.");
        endWork("done");
        return;
      }

      beginWork("creating");
      setBusy("Creating product on Daraz…");
      setExecutedCreate(true);
      const res = await callApi({
        execute: true,
        confirm: true,
        edit_before: false,
        destination_store_ids: destIds,
        resume_by_store: options?.resumeByStore,
      });
      setResult(res);
      ingestResultMeta(res);
      if (res.draft_result) {
        setDraftPreview(res.draft_result as Record<string, unknown>);
      } else {
        const destDraft = res.destinations?.find((d) => d.draft_preview);
        if (destDraft?.draft_preview) {
          setDraftPreview({ draft: destDraft.draft_preview } as Record<string, unknown>);
        }
      }
      applyResultMessages(res);
      endWork("done");
      for (const d of res.destinations || []) {
        if (d.attempt_id) {
          void pollAttemptIntoResult(d.attempt_id);
        }
      }
    } catch (err) {
      const diag =
        err instanceof ApiError
          ? extractDiagnosticId(err) ||
            extractDiagnosticId({ detail: (err as ApiError).detail })
          : null;
      if (diag) setCorrelationId(diag);
      const base = err instanceof Error ? err.message : "Add product failed";
      setAlerts("error", diag ? `${base} (ID ${diag})` : base);
      endWork("error");
    } finally {
      inFlightRef.current = false;
      setBusy("");
      setSubmitting(false);
    }
  }

  function applyResultMessages(res: AddProductResponse) {
    const summary = buildAttentionSummary(res.destinations, {
      diagnosticId: res.correlation_id || correlationId,
    });
    if (res.status === "BLOCKED_CREATE") {
      setAlerts(
        "warn",
        `Validated ${res.destinations?.length || 0} store(s). Create is blocked (ALLOW_PRODUCT_CREATE off) — nothing was created.`
      );
      return;
    }
    if (res.created_count) {
      const extra = summary.headline ? ` · ${summary.headline}` : "";
      setAlerts(
        summary.lines.length ? "warn" : "ok",
        `Created ${res.created_count}` +
          (res.failed_count ? ` · ${res.failed_count} failed` : "") +
          extra
      );
      return;
    }
    if (res.status === "ALREADY_EXISTS") {
      setAlerts("ok", "Product already exists on destination — skipped create");
      return;
    }
    if (res.needs_attention_count || summary.lines.length) {
      setAlerts("error", summary.headline || `${res.needs_attention_count} store(s) need attention`);
      return;
    }
    if (res.failed_count) {
      setAlerts("error", summary.headline || `${res.failed_count} store(s) failed — see details below`);
      return;
    }
    setAlerts("ok", res.status || "Done");
  }

  async function pollAttemptIntoResult(attemptId: string) {
    try {
      const attempt = await Api.getProductCreateAttempt(attemptId);
      setResult((prev) => {
        if (!prev?.destinations) return prev;
        const nextDest = prev.destinations.map((d) => {
          if (d.attempt_id !== attemptId) return d;
          return {
            ...d,
            attempt_id: attemptId,
            attempt_state: String(attempt.state || d.attempt_state || ""),
            creation_status: String(attempt.state || d.creation_status || d.status || ""),
            status:
              String(attempt.state) === "VERIFIED"
                ? "Created"
                : String(attempt.state || d.status),
            item_id: (attempt.destination_item_id as string | undefined) || d.item_id,
            reason: (attempt.last_error as string | undefined) || d.reason || undefined,
          };
        });
        return { ...prev, destinations: nextDest };
      });
    } catch {
      /* best-effort */
    }
  }

  async function onRetryDestination(d: AddProductDestinationResult) {
    if (!d.attempt_id || submitting || inFlightRef.current) return;
    if (!createGateOn) {
      setAlerts(
        "error",
        "Retry is blocked while ALLOW_PRODUCT_CREATE is off. Reconcile if needed; do not create."
      );
      return;
    }
    inFlightRef.current = true;
    beginWork("creating");
    setBusy(`Retrying ${d.store?.display_name || d.store?.store_id || "store"}…`);
    setLocalAlert(null);
    setSubmitting(true);
    try {
      const res = await Api.retryProductCreateAttempt(d.attempt_id, true);
      const attempt = (res.attempt as Record<string, unknown> | undefined) || res;
      const state = String(attempt.state || res.status || "");
      setResult((prev) => {
        if (!prev?.destinations) return prev;
        return {
          ...prev,
          destinations: prev.destinations.map((row) =>
            row.attempt_id === d.attempt_id
              ? {
                  ...row,
                  attempt_state: state,
                  creation_status: state,
                  status:
                    state === "VERIFIED"
                      ? "Created"
                      : state === "FAILED_SAFE_TO_RETRY"
                        ? "Failed"
                        : state,
                  item_id:
                    (attempt.destination_item_id as string | undefined) || row.item_id,
                  reason:
                    (attempt.last_error as string | undefined) ||
                    (res.reason as string | undefined) ||
                    row.reason,
                }
              : row
          ),
        };
      });
      setAlerts("ok", `Retry finished · ${state}`);
      endWork("done");
      void pollAttemptIntoResult(d.attempt_id);
    } catch (e) {
      setAlerts("error", e instanceof Error ? e.message : String(e));
      endWork("error");
    } finally {
      inFlightRef.current = false;
      setBusy("");
      setSubmitting(false);
    }
  }

  async function onReconcileDestination(d: AddProductDestinationResult) {
    if (!d.attempt_id || submitting || inFlightRef.current) return;
    beginWork("preparing");
    setBusy(`Reconciling ${d.store?.display_name || d.store?.store_id || "store"}…`);
    setLocalAlert(null);
    setSubmitting(true);
    try {
      const res = await Api.reconcileProductCreateAttempt(d.attempt_id);
      const attempt = (res.attempt as Record<string, unknown> | undefined) || res;
      const state = String(attempt.state || res.status || "");
      setResult((prev) => {
        if (!prev?.destinations) return prev;
        return {
          ...prev,
          destinations: prev.destinations.map((row) =>
            row.attempt_id === d.attempt_id
              ? {
                  ...row,
                  attempt_state: state,
                  creation_status: state,
                  status:
                    state === "VERIFIED"
                      ? "Created"
                      : state === "NEEDS_RECONCILIATION"
                        ? "NEEDS_ATTENTION"
                        : state,
                  item_id:
                    (attempt.destination_item_id as string | undefined) || row.item_id,
                  reason:
                    (attempt.last_error as string | undefined) ||
                    (res.reason as string | undefined) ||
                    row.reason,
                }
              : row
          ),
        };
      });
      setAlerts("ok", `Reconcile finished · ${state}`);
      endWork("done");
    } catch (e) {
      setAlerts("error", e instanceof Error ? e.message : String(e));
      endWork("error");
    } finally {
      setBusy("");
      setSubmitting(false);
    }
  }

  const primaryLabel = submitting
    ? phase === "fetching"
      ? "Fetching…"
      : phase === "preparing"
        ? "Preparing…"
        : phase === "creating"
          ? "Creating…"
          : "Working…"
    : editBefore
      ? "Prepare Preview"
      : "Add Product";

  const footer = (
    <div className="add-footer">
      <div className="add-footer__hint">
        {submitting ? (
          <span className="muted-line" aria-live="polite">
            {busy || "Request in progress…"}
            {elapsedMs != null ? ` · ${Math.round(elapsedMs / 1000)}s` : ""}
          </span>
        ) : brandBlocksCreate ? (
          <span className="muted-line">
            No Brand check did not confirm — create blocked. Re-check or fix category.
          </span>
        ) : createGateOn ? (
          <span className="muted-line">
            {editBefore
              ? "Preview only — CreateProduct will not run until you Add Product."
              : "Create runs only when you confirm Add Product."}
          </span>
        ) : (
          <span className="muted-line">
            Creation gate is off — fetch, preview, and Check No Brand still work.
          </span>
        )}
      </div>
      <div className="row" style={{ gap: "0.5rem", justifyContent: "flex-end", flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={resetAndClose}
          disabled={submitting}
        >
          {submitting ? "Close unavailable" : "Cancel"}
        </button>
        {mode === "url" ? (
          <button
            type="button"
            className="btn btn-secondary"
            disabled={submitting || !productUrl.trim() || !primaryDest}
            onClick={() => void onFetchProduct()}
          >
            {phase === "fetching" && submitting ? "Fetching…" : "Fetch Product"}
          </button>
        ) : null}
        <button
          type="button"
          className="btn btn-primary"
          disabled={editBefore ? !canPrepare : !canCreate}
          onClick={() => void runAdd({ edit: editBefore })}
        >
          {primaryLabel}
        </button>
      </div>
    </div>
  );

  return (
    <Dialog
      open={open}
      title="Add Product"
      subtitle="Add a Daraz listing to the selected destination store(s) with gated create and clear diagnostics."
      onClose={resetAndClose}
      size="drawer"
      closeOnBackdrop={!submitting}
      closeDisabled={submitting}
      footer={footer}
    >
      <div className="add-workspace">
        <div className="add-workspace__dest-chip">
          <span className="muted-line">Destination</span>
          {selectedStores.length === 0 ? (
            <StatusBadge tone="muted">None selected</StatusBadge>
          ) : selectedStores.length === 1 ? (
            <StatusBadge tone="ok">
              {selectedStores[0].display_name || selectedStores[0].store_name || selectedStores[0].store_id}
            </StatusBadge>
          ) : (
            <StatusBadge tone="ok">{selectedStores.length} stores</StatusBadge>
          )}
          {productCreateEnabled === false ? (
            <StatusBadge tone="warn">Create disabled</StatusBadge>
          ) : null}
        </div>

        {localAlert ? (
          <div
            className={`banner ${
              localAlert.tone === "ok"
                ? "banner-ok"
                : localAlert.tone === "warn"
                  ? "banner-warn"
                  : "banner-error"
            }`}
            role={localAlert.tone === "ok" ? "status" : "alert"}
          >
            {localAlert.text}
          </div>
        ) : null}

        {attention.lines.length > 0 ? (
          <div className="add-attention-list" role="alert">
            <h4>Stores needing attention</h4>
            <ul>
              {attention.lines.map((line) => (
                <li key={`${line.storeLabel}-${line.reasonLabel}`}>
                  <strong>{line.storeLabel}</strong> — {line.statusLabel}
                  <div className="add-attention-list__reason">{line.reasonLabel}</div>
                  <div className="muted-line">{line.recovery}</div>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="tabs" role="tablist">
          <button
            type="button"
            className={`tab${mode === "url" ? " active" : ""}`}
            onClick={() => {
              if (submitting) return;
              setMode("url");
              setResult(null);
            }}
            disabled={submitting}
          >
            Daraz Link
          </button>
          <button
            type="button"
            className={`tab${mode === "connected" ? " active" : ""}`}
            onClick={() => {
              if (submitting) return;
              setMode("connected");
              setResult(null);
              setFetchedUrl(null);
            }}
            disabled={submitting}
          >
            Connected Item ID
          </button>
        </div>

        {mode === "url" ? (
          <label className="add-field">
            Product URL
            <input
              value={productUrl}
              onChange={(e) => {
                setProductUrl(e.target.value);
                if (fetchedUrl && e.target.value.trim() !== fetchedUrl) {
                  setFetchedUrl(null);
                  setDraftPreview(null);
                  setExtractionComplete(null);
                }
              }}
              placeholder="https://www.daraz.pk/products/…"
              disabled={submitting}
            />
          </label>
        ) : (
          <div className="stack" style={{ gap: "0.65rem" }}>
            <label className="add-field">
              Source store
              <select
                value={sourceStore}
                onChange={(e) => setSourceStore(e.target.value)}
                disabled={submitting}
              >
                <option value="">Select store…</option>
                {stores.map((s) => (
                  <option key={s.store_id} value={s.store_id}>
                    {s.display_name || s.store_name || s.store_id}
                  </option>
                ))}
              </select>
            </label>
            <label className="add-field">
              Product / Item ID
              <input
                value={itemId}
                onChange={(e) => setItemId(e.target.value)}
                placeholder="e.g. 1974026524"
                inputMode="numeric"
                disabled={submitting}
              />
            </label>
          </div>
        )}

        <fieldset className="add-dest-select">
          <legend>Destination stores</legend>
          <div className="row" style={{ gap: "0.5rem", marginBottom: "0.45rem" }}>
            <button type="button" className="btn btn-ghost btn-sm" onClick={selectAllDest} disabled={submitting}>
              Select all
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={clearDest} disabled={submitting}>
              Clear
            </button>
          </div>
          <div className="add-dest-select__list">
            {destOptions.map((s) => (
              <label key={s.store_id} className="add-dest-select__row">
                <input
                  type="checkbox"
                  checked={destSelected.has(s.store_id)}
                  onChange={() => toggleDest(s.store_id)}
                  disabled={submitting}
                />
                <span>{s.display_name || s.store_name || s.store_id}</span>
              </label>
            ))}
          </div>
        </fieldset>

        {(draftPreview || productTitle) && (
          <section className="add-product-summary">
            <div className="add-product-summary__media">
              {thumb ? (
                <img src={thumb} alt="" className="add-product-summary__thumb" />
              ) : (
                <div className="add-product-summary__thumb add-product-summary__thumb--empty">No image</div>
              )}
            </div>
            <div className="add-product-summary__body">
              <h4>{productTitle || "Product preview"}</h4>
              <p className="muted-line" style={{ margin: 0 }}>
                {mode === "url" ? productUrl.trim() || "URL pending" : `Item ${itemId.trim() || "—"}`}
              </p>
              <div className="add-chip-row">
                <span className="add-chip">{draftVariants.length || 0} variants</span>
                <span className="add-chip">
                  {(
                    (draftPreview?.draft as { media?: { product_images?: string[] } } | undefined)
                      ?.media?.product_images || []
                  ).length}{" "}
                  images
                </span>
                {extractionComplete === false ? (
                  <span className="add-chip add-chip--warn">Incomplete extraction</span>
                ) : urlFetched ? (
                  <span className="add-chip add-chip--ok">Fetched</span>
                ) : null}
              </div>
              {draftVariants[0] ? (
                <p className="muted-line" style={{ margin: "0.35rem 0 0" }}>
                  PKR {String((draftVariants[0] as { price?: unknown }).price ?? "—")} · qty{" "}
                  {String((draftVariants[0] as { quantity?: unknown }).quantity ?? "—")} · SKU{" "}
                  <code>{String((draftVariants[0] as { seller_sku?: unknown }).seller_sku ?? "—")}</code>
                </p>
              ) : null}
            </div>
          </section>
        )}

        <label className="add-check">
          <input
            type="checkbox"
            checked={editBefore}
            onChange={(e) => setEditBefore(e.target.checked)}
            disabled={submitting}
          />
          Review draft before creating
        </label>

        <label className="add-field">
          Price override (optional — single SKU / same-price variants only)
          <input
            value={priceOverride}
            onChange={(e) => setPriceOverride(e.target.value)}
            placeholder="Will not flatten differently priced variants"
            inputMode="decimal"
            disabled={submitting}
          />
        </label>

        {unresolvedVariants.length ? (
          <fieldset className="add-price-required">
            <legend>Price information required</legend>
            <p className="muted-line">
              Enter regular price only for unresolved variants, then Continue Adding.
            </p>
            <div className="stack" style={{ gap: "0.5rem" }}>
              {unresolvedVariants.map((v) => (
                <label key={v.key} className="add-field">
                  {v.label}
                  <input
                    value={variantPrices[v.key] || ""}
                    onChange={(e) =>
                      setVariantPrices((prev) => ({ ...prev, [v.key]: e.target.value }))
                    }
                    placeholder="Rs."
                    inputMode="decimal"
                    disabled={submitting}
                  />
                </label>
              ))}
            </div>
            <button
              type="button"
              className="btn btn-primary"
              style={{ marginTop: "0.75rem" }}
              disabled={!canCreate}
              onClick={() => void runAdd({ edit: false })}
            >
              Continue Adding
            </button>
          </fieldset>
        ) : null}

        <ProcessingTimeline
          phase={phase}
          hasResult={Boolean(result) || Boolean(draftPreview)}
          executedCreate={executedCreate && phase !== "preparing"}
          elapsedMs={elapsedMs}
        />

        <div className="add-dest-results">
          <h4>Destination status</h4>
          {result?.destinations?.length ? (
            <div className="stack" style={{ gap: "0.65rem" }}>
              {result.destinations.map((d, i) => {
                const sid = String(d.store?.store_id || "");
                return (
                  <DestinationStatusCard
                    key={`${sid || i}-${d.attempt_id || d.status}`}
                    dest={d}
                    processing={submitting && phase === "creating"}
                    actionsDisabled={submitting || brandCheckingStoreId != null}
                    retryDisabled={!createGateOn}
                    onRetry={() => void onRetryDestination(d)}
                    onReconcile={() => void onReconcileDestination(d)}
                    noBrand={sid ? noBrandByStore[sid] : undefined}
                    noBrandChecking={Boolean(sid) && brandCheckingStoreId === sid}
                    noBrandDisabled={!canCheckNoBrand(previewCategoryId)}
                    noBrandDisabledReason={
                      canCheckNoBrand(previewCategoryId)
                        ? undefined
                        : "Needs preview primary_category_id — never guessed"
                    }
                    onCheckNoBrand={
                      sid ? () => void onCheckNoBrand(sid) : undefined
                    }
                  />
                );
              })}
            </div>
          ) : selectedStores.length ? (
            <div className="stack" style={{ gap: "0.65rem" }}>
              {selectedStores.map((s) => (
                <DestinationStatusCard
                  key={s.store_id}
                  dest={{
                    store: { store_id: s.store_id, display_name: s.display_name || s.store_name },
                    status: submitting ? (phase === "fetching" ? "Checking" : "Processing") : "READY",
                  }}
                  checking={submitting && phase === "fetching"}
                  processing={submitting && (phase === "creating" || phase === "preparing")}
                  actionsDisabled={submitting || brandCheckingStoreId != null}
                  noBrand={noBrandByStore[s.store_id]}
                  noBrandChecking={brandCheckingStoreId === s.store_id}
                  noBrandDisabled={!canCheckNoBrand(previewCategoryId)}
                  noBrandDisabledReason={
                    canCheckNoBrand(previewCategoryId)
                      ? undefined
                      : "Needs preview primary_category_id — never guessed"
                  }
                  onCheckNoBrand={() => void onCheckNoBrand(s.store_id)}
                />
              ))}
            </div>
          ) : (
            <p className="muted-line">Select a destination to see readiness and outcomes.</p>
          )}
        </div>

        <DiagnosticsPanel
          correlationId={correlationId || result?.correlation_id}
          phase={phase}
          message={localAlert?.text}
          destinations={
            result?.destinations ||
            selectedStores.map((s) => ({
              store: { store_id: s.store_id, display_name: s.display_name || s.store_name },
              status: "READY",
            }))
          }
          stageHistory={stageHistory}
          elapsedMs={elapsedMs}
          requestRunning={submitting || brandCheckingStoreId != null}
          noBrandByStore={noBrandByStore}
          primaryCategoryId={previewCategoryId}
        />

        {draftPreview ? (
          <details className="add-draft-details">
            <summary>Full draft preview</summary>
            <CloneDraftPreview
              result={draftPreview as any}
              variants={draftVariants as any}
              extractionComplete={extractionComplete}
            />
          </details>
        ) : null}
      </div>
    </Dialog>
  );
}
