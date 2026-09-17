import { useMemo, useState } from "react";
import {
  useAddProductFromConnected,
  useAddProductFromUrl,
} from "@/hooks/queries/useProducts";
import type { AddProductResponse, StoreView } from "@/types/api";
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

  async function runAdd(options?: {
    destIds?: string[];
    execute?: boolean;
    confirm?: boolean;
    edit?: boolean;
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
    }) {
      if (mode === "url") {
        return addUrlMutation.mutateAsync({
          url: productUrl.trim(),
          destination_store_ids: body.destination_store_ids,
          price_override: price,
          execute: body.execute,
          confirm: body.confirm,
          edit_before: body.edit_before,
        });
      }
      return addConnectedMutation.mutateAsync({
        source_store_id: sourceStore,
        daraz_item_id: itemId.trim(),
        destination_store_ids: body.destination_store_ids,
        price_override: price,
        execute: body.execute,
        confirm: body.confirm,
        edit_before: body.edit_before,
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
        onOk("Draft ready — review below. Create still respects the product-create gate.");
        return;
      }

      // Forced execute path (Retry Failed when gate already known on)
      if (options?.execute && options?.confirm) {
        setBusy("Creating products on selected stores…");
        const res = await callApi({
          execute: true,
          confirm: true,
          edit_before: false,
          destination_store_ids: destIds,
        });
        setResult(res);
        applyResultMessages(res);
        return;
      }

      setBusy("Fetching product… Validating destinations…");
      const analyzed = await callApi({
        execute: false,
        confirm: false,
        edit_before: false,
        destination_store_ids: destIds,
      });
      setResult(analyzed);

      if (!analyzed.product_create_enabled) {
        onOk(
          `Validated ${analyzed.destinations?.length || 0} store(s) · create blocked pending supervised proof`
        );
        onError(
          "Product create is disabled (ALLOW_PRODUCT_CREATE / ALLOW_PRODUCT_CREATE_PROBE). Validation results are shown below — nothing was created."
        );
        return;
      }

      const readyIds = (analyzed.destinations || [])
        .filter((d) => d.status === "READY" && d.store?.store_id)
        .map((d) => String(d.store!.store_id));

      if (!readyIds.length) {
        applyResultMessages(analyzed);
        return;
      }

      const ok = window.confirm(
        `Validation passed for ${readyIds.length} store(s). Create products now?`
      );
      if (!ok) {
        onOk("Validation complete — create not submitted.");
        return;
      }

      setBusy("Creating products…");
      const created = await callApi({
        execute: true,
        confirm: true,
        edit_before: false,
        destination_store_ids: readyIds,
      });
      setResult(created);
      applyResultMessages(created);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Add product failed");
    } finally {
      setBusy("");
    }
  }

  function applyResultMessages(res: AddProductResponse) {
    if (res.status === "BLOCKED_CREATE") {
      onOk(
        `Validated ${res.destinations?.length || 0} store(s) · create blocked pending supervised proof`
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
    } else if (res.needs_attention_count) {
      onError(
        `${res.needs_attention_count} store(s) need attention` +
          (res.destinations?.some((d) => d.reason === "missing_price")
            ? " — set a price override and retry"
            : "")
      );
    } else {
      onOk(res.status || "Analyzed");
    }
  }

  const failedStores = (result?.destinations || []).filter((d) =>
    ["Failed", "FAILED", "NEEDS_ATTENTION"].includes(String(d.status))
  );

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
        Price override (optional)
        <input
          value={priceOverride}
          onChange={(e) => setPriceOverride(e.target.value)}
          placeholder="Required when price is missing"
          inputMode="decimal"
        />
      </label>

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
          <h4 style={{ margin: "0 0 0.5rem" }}>Per-store results</h4>
          <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {result.destinations.map((d, i) => (
              <li key={`${d.store?.store_id || i}-${d.status}`}>
                <strong>{d.store?.display_name || d.store?.store_id || "Store"}</strong> —{" "}
                {d.status}
                {d.item_id ? ` · item ${d.item_id}` : ""}
                {d.reason ? ` · ${d.reason}` : ""}
              </li>
            ))}
          </ul>
          {failedStores.length ? (
            <div className="row" style={{ marginTop: "0.75rem" }}>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={Boolean(busy)}
                onClick={() =>
                  void runAdd({
                    destIds: failedStores
                      .map((d) => d.store?.store_id)
                      .filter(Boolean) as string[],
                    execute: Boolean(result.product_create_enabled),
                    confirm: Boolean(result.product_create_enabled),
                  })
                }
              >
                Retry Failed {failedStores.length}
              </button>
            </div>
          ) : null}
          {result.timings_ms?.total != null ? (
            <p className="muted-line" style={{ marginBottom: 0 }}>
              {Math.round(result.timings_ms.total)}ms
              {result.timings_ms.fetch_extract != null
                ? ` · fetch ${Math.round(result.timings_ms.fetch_extract)}ms`
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
