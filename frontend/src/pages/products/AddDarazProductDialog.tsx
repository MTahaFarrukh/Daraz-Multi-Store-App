import { useMemo, useState } from "react";
import {
  useAddProductFromConnected,
  useAddProductFromUrl,
  useImportUrlDraft,
} from "@/hooks/queries/useProducts";
import { Api } from "@/lib/api";
import {
  destinationActionKind,
  destinationProgressLabel,
  destinationReasonLabel,
  shouldShowReconcile,
  shouldShowRetry,
} from "@/lib/productAddProgress";
import type { AddProductDestinationResult, AddProductResponse, StoreView } from "@/types/api";
import { CloneDraftPreview } from "@/pages/products/CloneDraftPreview";
import { Dialog } from "@/components/ui/Dialog";

type SourceMode = "url" | "connected";

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
  const [draftPreview, setDraftPreview] = useState<Record<string, unknown> | null>(
    null
  );
  const [fetchedUrl, setFetchedUrl] = useState<string | null>(null);
  const [extractionComplete, setExtractionComplete] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const addUrlMutation = useAddProductFromUrl(workspaceId);
  const addConnectedMutation = useAddProductFromConnected(workspaceId);
  const importDraftMutation = useImportUrlDraft(workspaceId);

  const destOptions = useMemo(
    () =>
      stores.filter((s) => (mode === "connected" ? s.store_id !== sourceStore : true)),
    [stores, sourceStore, mode]
  );

  const primaryDest = useMemo(() => {
    const first = Array.from(destSelected)[0];
    return first || "";
  }, [destSelected]);

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

  const canAdd =
    !busy &&
    !submitting &&
    destSelected.size > 0 &&
    (mode === "connected"
      ? Boolean(sourceStore && itemId.trim())
      : urlFetched && Boolean(productUrl.trim()));

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
    setMode("url");
    setProductUrl("");
    setSourceStore("");
    setItemId("");
    setDestSelected(new Set());
    setEditBefore(true);
    setPriceOverride("");
    setVariantPrices({});
    setResult(null);
    setDraftPreview(null);
    setFetchedUrl(null);
    setExtractionComplete(null);
    setSubmitting(false);
    onClose();
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

  async function onFetchProduct() {
    if (mode !== "url") return;
    const url = productUrl.trim();
    if (!url) {
      onError("Paste a Daraz product URL first");
      return;
    }
    if (!primaryDest) {
      onError("Select at least one destination store before fetching");
      return;
    }
    onError("");
    onOk("");
    setResult(null);
    setBusy("Fetching product…");
    try {
      const res = await importDraftMutation.mutateAsync({
        url,
        destination_store_id: primaryDest,
      });
      setDraftPreview(res as unknown as Record<string, unknown>);
      setFetchedUrl(url);
      const extracted = (res as { extraction_complete?: boolean; draft?: { validation?: { warnings?: string[] } } });
      const complete =
        typeof extracted.extraction_complete === "boolean"
          ? extracted.extraction_complete
          : !(extracted.draft?.validation?.warnings || []).some((w) =>
              String(w).toLowerCase().includes("incomplete")
            );
      setExtractionComplete(complete);
      const warnCount = (res.warnings || []).length + (res.errors || []).length;
      if (complete && !warnCount) {
        onOk("Preview ready — review details, then Add Product.");
      } else if (complete) {
        onOk("Preview ready with notes — review warnings before adding.");
      } else {
        onError(
          "Extraction incomplete — missing fields are shown below. Fix what you can before adding."
        );
      }
    } catch (err) {
      setDraftPreview(null);
      setFetchedUrl(null);
      setExtractionComplete(null);
      onError(err instanceof Error ? err.message : "Could not fetch product");
    } finally {
      setBusy("");
    }
  }

  async function runAdd(options?: {
    destIds?: string[];
    execute?: boolean;
    confirm?: boolean;
    edit?: boolean;
    resumeByStore?: Record<string, string>;
  }) {
    if (submitting) return;
    const destIds = options?.destIds ?? Array.from(destSelected);
    if (!destIds.length) {
      onError("Select at least one destination store");
      return;
    }
    if (mode === "url" && !urlFetched) {
      onError("Fetch the product first so you can review the preview");
      return;
    }
    onError("");
    onOk("");
    setResult(null);

    const wantEdit = options?.edit ?? false;
    const price = parsePrice();
    const variantOverrides = parseVariantOverrides();

    if (mode === "url" && !productUrl.trim()) {
      onError("Product URL is required");
      return;
    }
    if (mode === "connected" && (!sourceStore || !itemId.trim())) {
      onError("Source store and Item ID are required");
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

    setSubmitting(true);
    try {
      if (wantEdit || editBefore) {
        setBusy("Preparing draft for edit…");
        const res = await callApi({
          execute: false,
          confirm: false,
          edit_before: true,
          destination_store_ids: destIds,
        });
        setResult(res);
        if (res.draft_result) {
          setDraftPreview(res.draft_result as Record<string, unknown>);
        }
        onOk("Draft ready — review below, then Add Product to create.");
        return;
      }

      setBusy("Creating product on Daraz…");
      const res = await callApi({
        execute: true,
        confirm: true,
        edit_before: false,
        destination_store_ids: destIds,
        resume_by_store: options?.resumeByStore,
      });
      setResult(res);
      if (res.draft_result) {
        setDraftPreview(res.draft_result as Record<string, unknown>);
      } else {
        const destDraft = res.destinations?.find((d) => d.draft_preview);
        if (destDraft?.draft_preview) {
          setDraftPreview({ draft: destDraft.draft_preview } as Record<string, unknown>);
        }
      }
      applyResultMessages(res);
      for (const d of res.destinations || []) {
        if (d.attempt_id) {
          void pollAttemptIntoResult(d.attempt_id, d.store?.store_id || null);
        }
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : "Add product failed");
    } finally {
      setBusy("");
      setSubmitting(false);
    }
  }

  function applyResultMessages(res: AddProductResponse) {
    if (res.status === "BLOCKED_CREATE") {
      onOk(
        `Validated ${res.destinations?.length || 0} store(s) · create blocked (ALLOW_PRODUCT_CREATE off)`
      );
      onError(
        "Product create is disabled. Validation results are shown below — nothing was created."
      );
    } else if (res.created_count) {
      onOk(
        `Created ${res.created_count}` +
          (res.failed_count ? ` · ${res.failed_count} failed` : "") +
          (res.needs_attention_count
            ? ` · ${res.needs_attention_count} need attention`
            : "")
      );
    } else if (res.status === "ALREADY_EXISTS") {
      onOk("Product already exists on destination — skipped create");
    } else if (res.needs_attention_count) {
      const missing = res.destinations?.some((d) => d.reason === "missing_price");
      const recon = res.destinations?.some(
        (d) =>
          String(d.creation_status || "").includes("RECONCIL") ||
          String(d.reason || "").includes("reconcil") ||
          String(d.reason || "").includes("claim")
      );
      onError(
        `${res.needs_attention_count} store(s) need attention` +
          (missing
            ? " — enter prices for unresolved variants (or a single override when all SKUs share one price)"
            : recon
              ? " — wait or use Reconcile; do not create again until resolved"
              : "")
      );
    } else if (res.failed_count) {
      onError(`${res.failed_count} store(s) failed — see details below`);
    } else {
      onOk(res.status || "Done");
    }
  }

  const retryable = (result?.destinations || []).filter((d) => shouldShowRetry(d));
  const reconcileable = (result?.destinations || []).filter((d) =>
    shouldShowReconcile(d)
  );

  async function pollAttemptIntoResult(attemptId: string, _storeId: string | null) {
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
            creation_status: String(
              attempt.state || d.creation_status || d.status || ""
            ),
            status:
              String(attempt.state) === "VERIFIED"
                ? "Created"
                : String(attempt.state || d.status),
            item_id:
              (attempt.destination_item_id as string | undefined) || d.item_id,
            reason:
              (attempt.last_error as string | undefined) || d.reason || undefined,
          };
        });
        return { ...prev, destinations: nextDest };
      });
    } catch {
      /* poll is best-effort */
    }
  }

  async function onRetryDestination(d: AddProductDestinationResult) {
    if (!d.attempt_id || submitting) return;
    setBusy(`Retrying ${d.store?.display_name || d.store?.store_id || "store"}…`);
    onError("");
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
                    (attempt.destination_item_id as string | undefined) ||
                    row.item_id,
                  reason:
                    (attempt.last_error as string | undefined) ||
                    (res.reason as string | undefined) ||
                    row.reason,
                }
              : row
          ),
        };
      });
      onOk(`Retry finished · ${destinationProgressLabel({ creation_status: state })}`);
      void pollAttemptIntoResult(d.attempt_id, d.store?.store_id || null);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
      setSubmitting(false);
    }
  }

  async function onReconcileDestination(d: AddProductDestinationResult) {
    if (!d.attempt_id || submitting) return;
    setBusy(`Reconciling ${d.store?.display_name || d.store?.store_id || "store"}…`);
    onError("");
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
                    (attempt.destination_item_id as string | undefined) ||
                    row.item_id,
                  reason:
                    (attempt.last_error as string | undefined) ||
                    (res.reason as string | undefined) ||
                    row.reason,
                }
              : row
          ),
        };
      });
      onOk(
        `Reconcile finished · ${destinationProgressLabel({ creation_status: state })}`
      );
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
      setSubmitting(false);
    }
  }

  const draftVariants =
    ((draftPreview?.draft as Record<string, unknown> | undefined)?.variants as Array<
      Record<string, unknown>
    >) || [];

  return (
    <Dialog open={open} title="Add Daraz Product" onClose={resetAndClose}>
      <div className="tabs" role="tablist" style={{ marginBottom: "0.75rem" }}>
        <button
          type="button"
          className={`tab${mode === "url" ? " active" : ""}`}
          onClick={() => {
            setMode("url");
            setResult(null);
          }}
        >
          Daraz Link
        </button>
        <button
          type="button"
          className={`tab${mode === "connected" ? " active" : ""}`}
          onClick={() => {
            setMode("connected");
            setResult(null);
            setFetchedUrl(null);
          }}
        >
          Connected Item ID
        </button>
      </div>

      {mode === "url" ? (
        <label style={{ display: "block" }}>
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
            disabled={Boolean(busy) || submitting}
          />
        </label>
      ) : (
        <>
          <label>
            Source Store
            <select
              value={sourceStore}
              onChange={(e) => setSourceStore(e.target.value)}
              disabled={Boolean(busy) || submitting}
            >
              <option value="">Select store…</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.display_name || s.store_name || s.store_id}
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: "block", marginTop: "0.5rem" }}>
            Product / Item ID
            <input
              value={itemId}
              onChange={(e) => setItemId(e.target.value)}
              placeholder="e.g. 1974026524"
              inputMode="numeric"
              disabled={Boolean(busy) || submitting}
            />
          </label>
        </>
      )}

      <fieldset style={{ border: "none", padding: 0, margin: "0.75rem 0" }}>
        <legend className="muted-line" style={{ padding: 0, marginBottom: "0.35rem" }}>
          Destination stores
        </legend>
        <div className="row" style={{ marginBottom: "0.35rem", gap: "0.5rem" }}>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={selectAllDest}
            disabled={Boolean(busy) || submitting}
          >
            Select All
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={clearDest}
            disabled={Boolean(busy) || submitting}
          >
            Clear
          </button>
        </div>
        <div className="stack" style={{ gap: "0.25rem", maxHeight: "10rem", overflowY: "auto" }}>
          {destOptions.map((s) => (
            <label key={s.store_id} className="row" style={{ gap: "0.5rem" }}>
              <input
                type="checkbox"
                checked={destSelected.has(s.store_id)}
                onChange={() => toggleDest(s.store_id)}
                disabled={Boolean(busy) || submitting}
              />
              {s.display_name || s.store_name || s.store_id}
            </label>
          ))}
        </div>
      </fieldset>

      {mode === "url" ? (
        <div className="row" style={{ gap: "0.5rem", marginBottom: "0.75rem" }}>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={Boolean(busy) || submitting || !productUrl.trim() || !primaryDest}
            onClick={() => void onFetchProduct()}
          >
            {busy.startsWith("Fetching") ? "Fetching…" : "Fetch Product"}
          </button>
          {urlFetched ? (
            <span className="muted-line">
              {extractionComplete === false
                ? "Preview loaded (incomplete extraction)"
                : "Preview loaded"}
            </span>
          ) : (
            <span className="muted-line">Fetch to preview before adding</span>
          )}
        </div>
      ) : null}

      <label className="row" style={{ gap: "0.5rem", marginBottom: "0.5rem" }}>
        <input
          type="checkbox"
          checked={editBefore}
          onChange={(e) => setEditBefore(e.target.checked)}
          disabled={Boolean(busy) || submitting}
        />
        Review draft before creating
      </label>

      <label style={{ display: "block", marginBottom: "0.75rem" }}>
        Price override (optional — single SKU / same-price variants only)
        <input
          value={priceOverride}
          onChange={(e) => setPriceOverride(e.target.value)}
          placeholder="Will not flatten differently priced variants"
          inputMode="decimal"
          disabled={Boolean(busy) || submitting}
        />
      </label>

      {unresolvedVariants.length ? (
        <fieldset
          style={{
            border: "1px solid var(--border, #ddd)",
            borderRadius: 6,
            padding: "0.75rem",
            marginBottom: "0.75rem",
          }}
        >
          <legend style={{ padding: "0 0.35rem" }}>Price information required</legend>
          <p className="muted-line" style={{ marginTop: 0 }}>
            Enter regular price only for unresolved variants, then Continue Adding.
          </p>
          <div className="stack" style={{ gap: "0.5rem" }}>
            {unresolvedVariants.map((v) => (
              <label key={v.key} style={{ display: "block" }}>
                {v.label}
                <input
                  value={variantPrices[v.key] || ""}
                  onChange={(e) =>
                    setVariantPrices((prev) => ({ ...prev, [v.key]: e.target.value }))
                  }
                  placeholder="Rs."
                  inputMode="decimal"
                  disabled={Boolean(busy) || submitting}
                />
              </label>
            ))}
          </div>
          <div className="row" style={{ marginTop: "0.75rem" }}>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!canAdd}
              onClick={() => void runAdd({ edit: false })}
            >
              Continue Adding
            </button>
          </div>
        </fieldset>
      ) : null}

      <div className="row" style={{ gap: "0.5rem" }}>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!canAdd}
          onClick={() => void runAdd({ edit: editBefore })}
        >
          {busy.includes("Creating") || busy.includes("Preparing")
            ? "Working…"
            : editBefore
              ? "Prepare Preview"
              : "Add Product"}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={resetAndClose}
          disabled={submitting}
        >
          Cancel
        </button>
      </div>

      {busy ? (
        <p className="muted-line" style={{ marginTop: "0.75rem" }} aria-live="polite">
          {busy}
        </p>
      ) : null}

      {result?.status === "BLOCKED_CREATE" ? (
        <p className="muted-line" style={{ marginTop: "0.75rem" }}>
          Create is blocked until ALLOW_PRODUCT_CREATE (or probe) is enabled after supervised
          proof. Per-store validation below is still accurate.
        </p>
      ) : null}

      {result?.destinations?.length ? (
        <section style={{ marginTop: "1rem" }}>
          <h4 style={{ margin: "0 0 0.5rem" }}>Per-store progress</h4>
          <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {result.destinations.map((d, i) => {
              const action = destinationActionKind(d);
              const reasonText = destinationReasonLabel(d.reason);
              return (
                <li
                  key={`${d.store?.store_id || i}-${d.attempt_id || d.status}`}
                  style={{ marginBottom: "0.35rem" }}
                >
                  <strong>{d.store?.display_name || d.store?.store_id || "Store"}</strong>{" "}
                  — {destinationProgressLabel(d)}
                  {d.item_id ? ` · item ${d.item_id}` : ""}
                  {d.existing_daraz_item_id
                    ? ` · existing ${d.existing_daraz_item_id}`
                    : ""}
                  {d.used_no_brand ? " · No Brand" : ""}
                  {reasonText ? ` · ${reasonText}` : ""}
                  {action === "retry" ? (
                    <button
                      type="button"
                      className="btn btn-ghost"
                      style={{ marginLeft: "0.5rem" }}
                      disabled={Boolean(busy) || submitting}
                      onClick={() => void onRetryDestination(d)}
                    >
                      Retry
                    </button>
                  ) : null}
                  {action === "reconcile" ? (
                    <button
                      type="button"
                      className="btn btn-ghost"
                      style={{ marginLeft: "0.5rem" }}
                      disabled={Boolean(busy) || submitting}
                      onClick={() => void onReconcileDestination(d)}
                    >
                      Reconcile
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {retryable.length || reconcileable.length ? (
            <p className="muted-line" style={{ marginTop: "0.5rem" }}>
              {retryable.length
                ? `${retryable.length} store(s) safe to retry (keeps Seller SKUs). `
                : ""}
              {reconcileable.length
                ? `${reconcileable.length} store(s) need reconciliation — do not create again until resolved.`
                : ""}
            </p>
          ) : null}
          {result.timings_ms?.total != null || result.timings_ms?.total_ms != null ? (
            <p className="muted-line" style={{ marginBottom: 0 }}>
              {Math.round(
                Number(result.timings_ms.total_ms ?? result.timings_ms.total) || 0
              )}
              ms
              {result.timings_ms.destination_parallelism != null
                ? ` · parallel ${result.timings_ms.destination_parallelism}`
                : ""}
              {result.timings_ms.catalog_strategy
                ? ` · catalog ${String(result.timings_ms.catalog_strategy)}`
                : ""}
              {result.timings_ms.fetch_extract != null
                ? ` · fetch ${Math.round(Number(result.timings_ms.fetch_extract) || 0)}ms`
                : ""}
            </p>
          ) : null}
        </section>
      ) : null}

      {draftPreview ? (
        <CloneDraftPreview
          result={draftPreview as any}
          variants={draftVariants as any}
          extractionComplete={extractionComplete}
        />
      ) : null}
    </Dialog>
  );
}
