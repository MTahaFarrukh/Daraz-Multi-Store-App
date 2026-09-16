import { useMemo, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { useStores } from "@/hooks/queries/useStores";
import {
  usePrepareCloneDraft,
  useProduct,
  useProducts,
  useSyncProducts,
} from "@/hooks/queries/useProducts";
import { Dialog } from "@/components/ui/Dialog";
import {
  EmptyState,
  ErrorBanner,
  LoadingState,
  PageHeader,
  StatusBadge,
  SuccessBanner,
} from "@/components/ui/Primitives";
import type { ProductCloneDraftResponse, ProductRow } from "@/types/api";
import { sanitizeProductHtml } from "@/lib/sanitizeHtml";
import { CopyProductDialog } from "@/pages/products/CopyProductDialog";
import { CloneDraftPreview } from "@/pages/products/CloneDraftPreview";

const PAGE_SIZE = 40;

function formatMoney(min?: number | null, max?: number | null): string {
  if (min == null && max == null) return "—";
  if (min != null && max != null && min !== max) return `PKR ${min} – ${max}`;
  return `PKR ${min ?? max}`;
}

function formatWhen(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

export function ProductsPage() {
  const { me } = useAuth();
  const workspaceId = me?.workspace?.id;
  const storesQuery = useStores(workspaceId);
  const stores = storesQuery.data || [];

  const [storeScope, setStoreScope] = useState("all");
  const [status, setStatus] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [detailId, setDetailId] = useState<string | null>(null);
  const [copyOpen, setCopyOpen] = useState(false);
  const [hubCopyProduct, setHubCopyProduct] = useState<ProductRow | null>(null);
  const [destStore, setDestStore] = useState("");
  const [draftResult, setDraftResult] = useState<ProductCloneDraftResponse | null>(null);

  const listFilters = useMemo(
    () => ({
      stores: storeScope === "all" ? undefined : storeScope,
      status: status || undefined,
      category_id: categoryId || undefined,
      search: search.trim() || undefined,
      page,
      page_size: PAGE_SIZE,
      sort: "synced_at_desc",
    }),
    [storeScope, status, categoryId, search, page]
  );

  const productsQuery = useProducts(workspaceId, listFilters);
  const syncMutation = useSyncProducts(workspaceId);
  const detailQuery = useProduct(workspaceId, detailId, Boolean(detailId));
  const cloneMutation = usePrepareCloneDraft(workspaceId);

  const products = productsQuery.data?.products || productsQuery.data?.items || [];
  const total = productsQuery.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const categories = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of products) {
      if (p.primary_category_id != null) {
        map.set(
          String(p.primary_category_id),
          p.primary_category_name || String(p.primary_category_id)
        );
      }
    }
    return Array.from(map.entries()).sort((a, b) => a[1].localeCompare(b[1]));
  }, [products]);

  async function handleSync() {
    setError("");
    setOk("");
    setBusy("Syncing catalog from Daraz…");
    try {
      const storeIds =
        storeScope === "all"
          ? undefined
          : stores.filter((s) => s.store_id === storeScope).map((s) => s.store_id);
      const result = await syncMutation.mutateAsync(
        storeIds?.length ? { store_ids: storeIds } : undefined
      );
      const upserted = (result.results || []).reduce(
        (sum, r) => sum + (r.products_upserted || 0),
        0
      );
      setOk(
        `Catalog synced · ${result.ok}/${result.stores} store(s)` +
          (upserted ? ` · ${upserted} product(s)` : "") +
          (result.failed ? ` · ${result.failed} failed` : "") +
          " · full details load on demand"
      );
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setBusy("");
    }
  }

  async function prepareHubCopy() {
    if (!hubCopyProduct || !destStore) {
      setError("Select a destination store");
      return;
    }
    setError("");
    setBusy("Preparing clone draft…");
    try {
      const result = await cloneMutation.mutateAsync({
        productId: hubCopyProduct.id,
        destinationStoreId: destStore,
      });
      setDraftResult(result);
      setOk("Clone draft ready — creation remains gated");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Clone draft failed");
    } finally {
      setBusy("");
    }
  }

  const draft = draftResult?.draft as Record<string, any> | undefined;
  const draftVariants = (draft?.variants as Array<Record<string, any>>) || [];

  return (
    <div className="stack">
      <PageHeader
        title="Products"
        description="Multi-store Product Hub from your connected Daraz catalogs. Sync the catalog, then copy by Item ID or Daraz link."
        actions={
          <div className="row" style={{ gap: "0.5rem" }}>
            <button
              type="button"
              className="btn btn-primary"
              disabled={Boolean(busy)}
              onClick={() => {
                setError("");
                setOk("");
                setCopyOpen(true);
              }}
            >
              + Copy Product
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={syncMutation.isPending || Boolean(busy)}
              onClick={handleSync}
            >
              Sync Products
            </button>
          </div>
        }
      />

      <ErrorBanner message={error} />
      <SuccessBanner message={ok} />
      {busy ? <div className="banner banner-info">{busy}</div> : null}

      <section className="card">
        <div className="row" style={{ flexWrap: "wrap", gap: "0.65rem" }}>
          <label>
            Store
            <select
              value={storeScope}
              onChange={(e) => {
                setStoreScope(e.target.value);
                setPage(1);
              }}
            >
              <option value="all">All Stores</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.display_name || s.store_name || s.store_id}
                </option>
              ))}
            </select>
          </label>
          <label>
            Status
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(1);
              }}
            >
              <option value="">Any</option>
              <option value="Active">Active</option>
              <option value="Inactive">Inactive</option>
              <option value="Deleted">Deleted</option>
            </select>
          </label>
          <label>
            Category
            <select
              value={categoryId}
              onChange={(e) => {
                setCategoryId(e.target.value);
                setPage(1);
              }}
            >
              <option value="">Any</option>
              {categories.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label style={{ flex: 1, minWidth: "12rem" }}>
            Search
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              placeholder="Search products…"
            />
          </label>
        </div>
      </section>

      {productsQuery.isLoading ? <LoadingState label="Loading products…" /> : null}
      {productsQuery.isError ? (
        <ErrorBanner
          message={
            productsQuery.error instanceof Error
              ? productsQuery.error.message
              : "Failed to load products"
          }
        />
      ) : null}

      {!productsQuery.isLoading && products.length === 0 ? (
        <EmptyState
          title="No products yet"
          description="Use + Copy Product for a single Item ID, or Sync Products to pull catalog listings (not full details)."
        />
      ) : null}

      {products.length ? (
        <section className="card" style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Image</th>
                <th>Product</th>
                <th>Store</th>
                <th>Variants</th>
                <th>Price</th>
                <th>Stock</th>
                <th>Status</th>
                <th>Last Sync</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.id}>
                  <td>
                    {p.primary_image ? (
                      <img
                        src={p.primary_image}
                        alt=""
                        width={48}
                        height={48}
                        style={{ objectFit: "cover", borderRadius: 6 }}
                      />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => setDetailId(p.id)}
                      style={{
                        background: "none",
                        border: 0,
                        padding: 0,
                        textAlign: "left",
                        cursor: "pointer",
                        color: "var(--teal-deep)",
                        fontWeight: 600,
                      }}
                    >
                      {p.title_en || p.title || p.daraz_item_id}
                    </button>
                    <div className="muted-line" style={{ fontSize: "0.75rem" }}>
                      #{p.daraz_item_id}
                    </div>
                  </td>
                  <td>
                    <strong>{p.store_display_name || p.store_slug || "—"}</strong>
                  </td>
                  <td>{p.variant_count ?? p.variants_count ?? "—"}</td>
                  <td>{formatMoney(p.price_min, p.price_max)}</td>
                  <td>{p.stock_total ?? "—"}</td>
                  <td>
                    <StatusBadge>{p.status_raw || "unknown"}</StatusBadge>
                  </td>
                  <td style={{ whiteSpace: "nowrap", fontSize: "0.8rem" }}>
                    {formatWhen(p.synced_at)}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => {
                        setHubCopyProduct(p);
                        setDestStore("");
                        setDraftResult(null);
                      }}
                    >
                      Copy
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row" style={{ marginTop: "0.75rem", justifyContent: "space-between" }}>
            <span className="muted-line">
              Page {page} / {pageCount} · {total} product(s)
            </span>
            <div className="row">
              <button
                type="button"
                className="btn btn-ghost"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={page >= pageCount}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          </div>
        </section>
      ) : null}

      <Dialog
        open={Boolean(detailId)}
        onClose={() => setDetailId(null)}
        title="Product detail"
      >
        {detailQuery.isLoading ? <LoadingState label="Loading…" /> : null}
        {detailQuery.data ? (
          <ProductDetailBody
            product={detailQuery.data.product}
            variants={detailQuery.data.variants}
            onCopy={() => {
              setHubCopyProduct(detailQuery.data!.product);
              setDestStore("");
              setDraftResult(null);
              setDetailId(null);
            }}
          />
        ) : null}
      </Dialog>

      <Dialog
        open={Boolean(hubCopyProduct)}
        onClose={() => {
          setHubCopyProduct(null);
          setDraftResult(null);
        }}
        title="Copy Product"
      >
        {hubCopyProduct ? (
          <div className="stack">
            <p style={{ margin: 0 }}>
              <strong>Source:</strong>{" "}
              {hubCopyProduct.store_display_name || hubCopyProduct.store_slug}
            </p>
            <p style={{ margin: 0 }}>{hubCopyProduct.title_en || hubCopyProduct.title}</p>
            <label>
              Destination Store
              <select value={destStore} onChange={(e) => setDestStore(e.target.value)}>
                <option value="">Select store…</option>
                {stores
                  .filter((s) => s.store_id !== hubCopyProduct.store_slug)
                  .map((s) => (
                    <option key={s.store_id} value={s.store_id}>
                      {s.display_name || s.store_name || s.store_id}
                    </option>
                  ))}
              </select>
            </label>
            <div className="row">
              <button
                type="button"
                className="btn btn-primary"
                disabled={!destStore || cloneMutation.isPending}
                onClick={prepareHubCopy}
              >
                Prepare Copy
              </button>
            </div>

            {draftResult ? (
              <CloneDraftPreview result={draftResult} variants={draftVariants} />
            ) : null}
          </div>
        ) : null}
      </Dialog>

      <CopyProductDialog
        open={copyOpen}
        onClose={() => setCopyOpen(false)}
        stores={stores}
        workspaceId={workspaceId}
        onError={setError}
        onOk={setOk}
        busy={busy}
        setBusy={setBusy}
      />
    </div>
  );
}

function ProductDetailBody({
  product,
  variants,
  onCopy,
}: {
  product: ProductRow;
  variants: Array<Record<string, any>>;
  onCopy: () => void;
}) {
  const images = (product.images_json || [])
    .map((i) => (typeof i === "string" ? i : i.url))
    .filter(Boolean) as string[];
  return (
    <div className="stack">
      <div className="row" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
        {images.slice(0, 8).map((src) => (
          <img
            key={src}
            src={src}
            alt=""
            width={72}
            height={72}
            style={{ objectFit: "cover", borderRadius: 8 }}
          />
        ))}
      </div>
      <p style={{ margin: 0 }}>
        <strong>{product.title_en || product.title}</strong>
      </p>
      <p className="muted-line" style={{ margin: 0 }}>
        Store: {product.store_display_name || product.store_slug} · Item{" "}
        {product.daraz_item_id}
      </p>
      <p style={{ margin: 0 }}>
        Category: {product.primary_category_name || product.primary_category_id || "—"} ·
        Brand: {product.brand || "—"}
      </p>
      <StatusBadge>{product.status_raw || "unknown"}</StatusBadge>
      {product.video_ref ? (
        <div className="banner banner-info">
          Video attached on source listing (id {product.video_ref}). Automatic video cloning
          is not supported.
        </div>
      ) : null}
      <div
        className="muted-line"
        style={{ maxHeight: 160, overflow: "auto", fontSize: "0.85rem" }}
        dangerouslySetInnerHTML={{
          __html: sanitizeProductHtml(
            product.description_html_safe ||
              product.description_en ||
              product.description ||
              ""
          ).slice(0, 4000),
        }}
      />
      <h4 style={{ margin: "0.5rem 0 0" }}>Variants</h4>
      <table className="data-table">
        <thead>
          <tr>
            <th>SellerSku</th>
            <th>saleProp</th>
            <th>Price</th>
            <th>Qty</th>
            <th>Weight</th>
            <th>L×W×H</th>
          </tr>
        </thead>
        <tbody>
          {variants.map((v) => (
            <tr key={v.id}>
              <td>{v.seller_sku}</td>
              <td style={{ fontSize: "0.75rem" }}>
                {JSON.stringify(v.sale_props_json || {})}
              </td>
              <td>{v.price}</td>
              <td>{v.quantity}</td>
              <td>{v.package_weight ?? "—"}</td>
              <td>
                {[v.package_length, v.package_width, v.package_height]
                  .map((x) => x ?? "—")
                  .join(" × ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="btn btn-primary" onClick={onCopy}>
        Copy
      </button>
    </div>
  );
}
