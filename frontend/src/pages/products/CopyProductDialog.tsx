import { useMemo, useState } from "react";
import {
  useCloneDraftFromConnected,
  useFetchConnectedProduct,
  useImportUrlDraft,
} from "@/hooks/queries/useProducts";
import type { ProductCloneDraftResponse, StoreView } from "@/types/api";
import { CloneDraftPreview } from "@/pages/products/CloneDraftPreview";
import { Dialog } from "@/components/ui/Dialog";

type SourceMode = "connected" | "url";

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

export function CopyProductDialog({
  open,
  onClose,
  stores,
  workspaceId,
  onError,
  onOk,
  busy,
  setBusy,
}: Props) {
  const [mode, setMode] = useState<SourceMode>("connected");
  const [sourceStore, setSourceStore] = useState("");
  const [itemId, setItemId] = useState("");
  const [productUrl, setProductUrl] = useState("");
  const [destStore, setDestStore] = useState("");
  const [fetched, setFetched] = useState<{
    product: Record<string, any>;
    variants: Array<Record<string, any>>;
    timings_ms?: Record<string, number>;
  } | null>(null);
  const [draftResult, setDraftResult] = useState<ProductCloneDraftResponse | null>(null);

  const fetchMutation = useFetchConnectedProduct(workspaceId);
  const cloneMutation = useCloneDraftFromConnected(workspaceId);
  const importMutation = useImportUrlDraft(workspaceId);

  const destOptions = useMemo(
    () => stores.filter((s) => (mode === "connected" ? s.store_id !== sourceStore : true)),
    [stores, sourceStore, mode]
  );

  async function handleFetch() {
    onError("");
    onOk("");
    setDraftResult(null);
    if (!sourceStore || !itemId.trim()) {
      onError("Select a source store and enter a Product / Item ID");
      return;
    }
    setBusy("Fetching product…");
    try {
      const result = await fetchMutation.mutateAsync({
        source_store_id: sourceStore,
        daraz_item_id: itemId.trim(),
      });
      setFetched({
        product: result.product as Record<string, any>,
        variants: (result.variants || []) as Array<Record<string, any>>,
        timings_ms: result.timings_ms,
      });
      const ms = result.timings_ms?.total;
      onOk(
        `Fetched item ${itemId.trim()}` +
          (ms != null ? ` · ${Math.round(ms)}ms` : "") +
          " · catalog sync not required"
      );
    } catch (err) {
      setFetched(null);
      onError(err instanceof Error ? err.message : "Fetch failed");
    } finally {
      setBusy("");
    }
  }

  async function handlePrepareConnected() {
    onError("");
    if (!sourceStore || !itemId.trim() || !destStore) {
      onError("Source store, Item ID, and destination store are required");
      return;
    }
    setBusy("Preparing variants… Validating category… Checking destination…");
    try {
      const result = await cloneMutation.mutateAsync({
        source_store_id: sourceStore,
        daraz_item_id: itemId.trim(),
        destination_store_id: destStore,
      });
      setDraftResult(result);
      onOk("Copy draft ready");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Prepare Copy failed");
    } finally {
      setBusy("");
    }
  }

  async function handleAnalyzeUrl() {
    onError("");
    onOk("");
    setDraftResult(null);
    setFetched(null);
    if (!productUrl.trim() || !destStore) {
      onError("Product URL and destination store are required");
      return;
    }
    setBusy("Analyzing Daraz product… Extracting images… Preparing copy draft…");
    try {
      const result = await importMutation.mutateAsync({
        url: productUrl.trim(),
        destination_store_id: destStore,
      });
      setDraftResult(result);
      onOk("Public link draft ready · Create Copy remains gated");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Analyze Product failed");
    } finally {
      setBusy("");
    }
  }

  function resetAndClose() {
    setMode("connected");
    setItemId("");
    setProductUrl("");
    setFetched(null);
    setDraftResult(null);
    onClose();
  }

  const draftVariants =
    ((draftResult?.draft as Record<string, any> | undefined)?.variants as Array<
      Record<string, any>
    >) || [];

  return (
    <Dialog open={open} title="Copy Product" onClose={resetAndClose}>
      <fieldset style={{ border: "none", padding: 0, margin: "0 0 0.75rem" }}>
        <legend className="muted-line" style={{ padding: 0 }}>
          How do you want to find the source?
        </legend>
        <label className="row" style={{ gap: "0.5rem", marginBottom: "0.35rem" }}>
          <input
            type="radio"
            name="copy-source"
            checked={mode === "connected"}
            onChange={() => setMode("connected")}
          />
          My Connected Store
        </label>
        <label className="row" style={{ gap: "0.5rem" }}>
          <input
            type="radio"
            name="copy-source"
            checked={mode === "url"}
            onChange={() => setMode("url")}
          />
          Daraz Product Link
        </label>
      </fieldset>

      {mode === "url" ? (
        <>
          <label style={{ display: "block" }}>
            Product URL
            <input
              value={productUrl}
              onChange={(e) => setProductUrl(e.target.value)}
              placeholder="https://www.daraz.pk/products/…"
            />
          </label>
          <label style={{ display: "block", marginTop: "0.5rem" }}>
            Destination Store
            <select value={destStore} onChange={(e) => setDestStore(e.target.value)}>
              <option value="">Select store…</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.display_name || s.store_name || s.store_id}
                </option>
              ))}
            </select>
          </label>
          <div className="row" style={{ marginTop: "0.75rem" }}>
            <button
              type="button"
              className="btn btn-primary"
              disabled={Boolean(busy) || !productUrl.trim() || !destStore}
              onClick={handleAnalyzeUrl}
            >
              Analyze Product
            </button>
          </div>
        </>
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
          <div className="row" style={{ marginTop: "0.75rem" }}>
            <button
              type="button"
              className="btn btn-primary"
              disabled={Boolean(busy) || !sourceStore || !itemId.trim()}
              onClick={handleFetch}
            >
              Fetch Product
            </button>
          </div>
        </>
      )}

      {fetched ? (
        <section style={{ marginTop: "1rem" }}>
          <h4 style={{ margin: "0 0 0.5rem" }}>Fetched preview</h4>
          <div className="row" style={{ gap: "0.75rem", alignItems: "flex-start" }}>
            {fetched.product.primary_image || (fetched.product.images_json || [])[0]?.url ? (
              <img
                src={
                  fetched.product.primary_image || (fetched.product.images_json || [])[0]?.url
                }
                alt=""
                width={72}
                height={72}
                style={{ objectFit: "cover", borderRadius: 8 }}
              />
            ) : null}
            <div>
              <strong>{fetched.product.title_en || fetched.product.title || "—"}</strong>
              <p className="muted-line" style={{ margin: "0.25rem 0" }}>
                Item {fetched.product.daraz_item_id} · {fetched.variants.length} variant(s)
              </p>
            </div>
          </div>
          {mode === "connected" ? (
            <>
              <label style={{ display: "block", marginTop: "0.75rem" }}>
                Destination Store
                <select value={destStore} onChange={(e) => setDestStore(e.target.value)}>
                  <option value="">Select store…</option>
                  {destOptions.map((s) => (
                    <option key={s.store_id} value={s.store_id}>
                      {s.display_name || s.store_name || s.store_id}
                    </option>
                  ))}
                </select>
              </label>
              <div className="row" style={{ marginTop: "0.75rem" }}>
                <button
                  type="button"
                  className="btn btn-accent"
                  disabled={Boolean(busy) || !destStore}
                  onClick={handlePrepareConnected}
                >
                  Prepare Copy
                </button>
              </div>
            </>
          ) : null}
        </section>
      ) : null}

      {draftResult ? <CloneDraftPreview result={draftResult} variants={draftVariants} /> : null}

      <p className="muted-line" style={{ marginTop: "0.75rem" }}>
        Create Copy remains gated until supervised CreateProduct proof succeeds.
      </p>
      <div className="row" style={{ marginTop: "0.5rem" }}>
        <button type="button" className="btn btn-ghost" onClick={resetAndClose}>
          Cancel
        </button>
        <button type="button" className="btn btn-ghost" disabled title="Gated">
          Create Copy (gated)
        </button>
      </div>
    </Dialog>
  );
}
