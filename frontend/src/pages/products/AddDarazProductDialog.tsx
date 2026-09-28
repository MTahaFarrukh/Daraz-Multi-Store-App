import { useMemo, useState } from "react";
import {
  useAddProductFromConnected,
  useAddProductFromUrl,
} from "@/hooks/queries/useProducts";
import { Api } from "@/lib/api";
import {
  destinationActionKind,
  destinationProgressLabel,
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

  const addUrlMutation = useAddProductFromUrl(workspaceId);
  const addConnectedMutation = useAddProductFromConnected(workspaceId);

  const destOptions = useMemo(
    () =>
      stores.filter((s) => (mode === "connected" ? s.store_id !== sourceStore : true)),
    [stores, sourceStore, mode]
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
    setEditBefore(false);
    setPriceOverride("");
    setVariantPrices({});
    setResult(null);
    setDraftPreview(null);
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

  async function runAdd(options?: {
    destIds?: string[];
    execute?: boolean;
    confirm?: boolean;
    edit?: boolean;
    resumeByStore?: Record<string, string>;
  }) {
    const destIds = options?.destIds ?? Array.from(destSelected);
    if (!destIds.length) {
      onError("Select at least one destination store");
      return;
    }
    onError("");
    onOk("");
    setResult(null);
    setDraftPreview(null);

    const wantEdit = options?.edit ?? editBefore;
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

    try {
      if (wantEdit) {
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
        onOk("Draft ready — review below.");
        return;
      }

      setBusy("Analyzing product… Creating on Daraz…");
      const res = await callApi({
        execute: true,
        confirm: true,
        edit_before: false,
        destination_store_ids: destIds,
        resume_by_store: options?.resumeByStore,
      });
      setResult(res);
      applyResultMessages(res);
      // Progressive attempt refresh when IDs are available (non-blocking).
      for (const d of res.destinations || []) {
        if (d.attempt_id) {
          void pollAttemptIntoResult(d.attempt_id, d.store?.store_id || null);
        }
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : "Add product failed");
    } finally {
      setBusy("");
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
      onError(
        `${res.needs_attention_count} store(s) need attention` +
          (missing
            ? " — enter prices for unresolved variants (or a single override when all SKUs share one price)"
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
    if (!d.attempt_id) return;
    setBusy(`Retrying ${d.store?.display_name || d.store?.store_id || "store"}…`);
    onError("");
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
      onOk(`Retry finished · ${state}`);
      void pollAttemptIntoResult(d.attempt_id, d.store?.store_id || null);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function onReconcileDestination(d: AddProductDestinationResult) {
    if (!d.attempt_id) return;
    setBusy(`Reconciling ${d.store?.display_name || d.store?.store_id || "store"}…`);
    onError("");
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
      onOk(`Reconcile finished · ${state}`);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
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
          onClick={() => setMode("url")}
        >
          Daraz Link
        </button>
        <button
          type="button"
          className={`tab${mode === "connected" ? " active" : ""}`}
          onClick={() => setMode("connected")}
        >
          Connected Item ID
        </button>
      </div>

      {mode === "url" ? (
        <label style={{ display: "block" }}>
          Product URL
          <input
            value={productUrl}
            onChange={(e) => setProductUrl(e.target.value)}
            placeholder="https://www.daraz.pk/products/…"
          />
        </label>
      ) : (
        <>
          <label>
            Source Store
            <select value={sourceStore} onChange={(e) => setSourceStore(e.target.value)}>
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
            />
          </label>
        </>
      )}

      <fieldset style={{ border: "none", padding: 0, margin: "0.75rem 0" }}>
        <legend className="muted-line" style={{ padding: 0, marginBottom: "0.35rem" }}>
          Destination stores
        </legend>
        <div className="row" style={{ marginBottom: "0.35rem", gap: "0.5rem" }}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={selectAllDest}>
            Select All
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={clearDest}>
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
              />
              {s.display_name || s.store_name || s.store_id}
            </label>
          ))}
        </div>
      </fieldset>

      <label className="row" style={{ gap: "0.5rem", marginBottom: "0.5rem" }}>
        <input
          type="checkbox"
          checked={editBefore}
          onChange={(e) => setEditBefore(e.target.checked)}
        />
        Edit before adding
      </label>

      <label style={{ display: "block", marginBottom: "0.75rem" }}>
        Price override (optional — single SKU / same-price variants only)
        <input
          value={priceOverride}
          onChange={(e) => setPriceOverride(e.target.value)}
          placeholder="Will not flatten differently priced variants"
          inputMode="decimal"
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
                />
              </label>
            ))}
          </div>
          <div className="row" style={{ marginTop: "0.75rem" }}>
            <button
              type="button"
              className="btn btn-primary"
              disabled={Boolean(busy) || !destSelected.size}
              onClick={() => void runAdd()}
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
          disabled={Boolean(busy) || !destSelected.size}
          onClick={() => void runAdd()}
        >
          Add Product
        </button>
        <button type="button" className="btn btn-ghost" onClick={resetAndClose}>
          Cancel
        </button>
      </div>

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
                  {d.reason ? ` · ${d.reason}` : ""}
                  {action === "retry" ? (
                    <button
                      type="button"
                      className="btn btn-ghost"
                      style={{ marginLeft: "0.5rem" }}
                      disabled={Boolean(busy)}
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
                      disabled={Boolean(busy)}
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
                ? `${reconcileable.length} store(s) need reconciliation.`
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
        />
      ) : null}
    </Dialog>
  );
}
