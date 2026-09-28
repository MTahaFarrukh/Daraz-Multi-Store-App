import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { useStores } from "@/hooks/queries/useStores";
import {
  EmptyState,
  ErrorBanner,
  LoadingState,
  PageHeader,
  StatCard,
  StatusBadge,
  SuccessBanner,
} from "@/components/ui/Primitives";
import {
  INVENTORY_EMPTY_MESSAGE,
  formatStockQuantity,
  stockBadgeForQuantity,
  stockBadgeTone,
  type StockBadge,
} from "@/lib/inventoryLabels";

function formatPrice(value: number | null | undefined): string {
  if (value == null) return "—";
  return `PKR ${Number(value).toLocaleString()}`;
}

function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

export function InventoryPage() {
  const { me } = useAuth();
  const workspaceId = me?.workspace?.id;
  const qc = useQueryClient();
  const storesQuery = useStores(workspaceId);
  const stores = storesQuery.data || [];

  const [search, setSearch] = useState("");
  const [storeId, setStoreId] = useState("");
  const [status, setStatus] = useState("");
  const [lowStock, setLowStock] = useState(false);
  const [threshold, setThreshold] = useState(5);
  const [sort, setSort] = useState("product");
  const [page, setPage] = useState(1);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const filters = {
    store_id: storeId || undefined,
    search: search.trim() || undefined,
    status: status || undefined,
    low_stock: lowStock || undefined,
    low_stock_threshold: threshold,
    sort,
    page,
    page_size: 50,
  };

  const listQuery = useQuery({
    queryKey: ["inventory", workspaceId, filters],
    queryFn: () => Api.listInventory(filters),
    enabled: Boolean(workspaceId),
  });

  const summaryQuery = useQuery({
    queryKey: ["inventory-summary", workspaceId, storeId, threshold],
    queryFn: () =>
      Api.inventorySummary({
        store_id: storeId || undefined,
        low_stock_threshold: threshold,
      }),
    enabled: Boolean(workspaceId),
  });

  const syncMutation = useMutation({
    mutationFn: () => {
      const ids = storeId
        ? [storeId]
        : stores.map((s) => s.store_id).filter(Boolean);
      return Api.syncProducts({
        store_ids: ids.length ? ids : undefined,
        fetch_details: true,
      });
    },
    onSuccess: () => {
      setMsg("Product sync started. Inventory will refresh shortly.");
      setErr("");
      void qc.invalidateQueries({ queryKey: ["inventory", workspaceId] });
      void qc.invalidateQueries({ queryKey: ["inventory-summary", workspaceId] });
    },
    onError: (e: unknown) => {
      setErr(e instanceof Error ? e.message : String(e));
      setMsg("");
    },
  });

  const items = listQuery.data?.items || [];
  const total = listQuery.data?.total || 0;
  const pageSize = listQuery.data?.page_size || 50;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const summary = summaryQuery.data;

  const empty = !listQuery.isLoading && total === 0 && !search && !status && !lowStock;

  const rows = useMemo(() => items, [items]);

  return (
    <div className="stack">
      <PageHeader
        title="Inventory"
        description="SKU-level stock from synced workspace products. Quantities show Unknown when not synced — never guessed."
        actions={
          <button
            type="button"
            className="btn btn-primary"
            disabled={syncMutation.isPending}
            onClick={() => syncMutation.mutate()}
          >
            {syncMutation.isPending ? "Syncing…" : "Sync Products"}
          </button>
        }
      />
      <ErrorBanner message={err || (listQuery.error instanceof Error ? listQuery.error.message : "")} />
      <SuccessBanner message={msg} />

      <div className="grid-stats">
        <StatCard label="Total Listings" value={summary?.total_listings ?? "…"} />
        <StatCard label="Total SKUs" value={summary?.total_skus ?? "…"} />
        <StatCard label="Active Listings" value={summary?.active_listings ?? "…"} />
        <StatCard
          label="Known Low Stock"
          value={summary?.known_low_stock_skus ?? "…"}
          hint={`Threshold ≤ ${threshold}`}
        />
        <StatCard label="Out of Stock" value={summary?.out_of_stock_skus ?? "…"} />
      </div>

      <section className="card">
        <div className="row filters-row" style={{ flexWrap: "wrap", gap: "0.75rem" }}>
          <label>
            Search
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              placeholder="Name, SKU, item ID"
            />
          </label>
          <label>
            Store
            <select
              value={storeId}
              onChange={(e) => {
                setStoreId(e.target.value);
                setPage(1);
              }}
            >
              <option value="">All stores</option>
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
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
              <option value="deleted">Deleted</option>
            </select>
          </label>
          <label>
            Sort
            <select value={sort} onChange={(e) => setSort(e.target.value)}>
              <option value="product">Product</option>
              <option value="quantity">Quantity</option>
              <option value="price">Price</option>
              <option value="last_synced">Last synced</option>
            </select>
          </label>
          <label>
            Low-stock ≤
            <input
              type="number"
              min={0}
              max={1000}
              value={threshold}
              onChange={(e) => {
                setThreshold(Number(e.target.value) || 5);
                setPage(1);
              }}
              style={{ width: "4.5rem" }}
            />
          </label>
          <label className="row" style={{ alignItems: "center", gap: "0.4rem", marginTop: "1.4rem" }}>
            <input
              type="checkbox"
              checked={lowStock}
              onChange={(e) => {
                setLowStock(e.target.checked);
                setPage(1);
              }}
            />
            Low stock only
          </label>
        </div>
      </section>

      {listQuery.isLoading ? <LoadingState label="Loading inventory…" /> : null}

      {empty ? (
        <EmptyState title={INVENTORY_EMPTY_MESSAGE} description="Connect stores and sync product catalog details." />
      ) : null}

      {!listQuery.isLoading && !empty ? (
        <section className="card">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Store</th>
                  <th>SKU / Variant</th>
                  <th>Price</th>
                  <th>Stock</th>
                  <th>Status</th>
                  <th>Last Updated</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const badge = (row.stock_badge ||
                    stockBadgeForQuantity(row.quantity, threshold)) as StockBadge;
                  return (
                    <tr key={String(row.variant_id || row.seller_sku)}>
                      <td>
                        <div className="row" style={{ gap: "0.6rem", alignItems: "center" }}>
                          {row.thumbnail_url ? (
                            <img
                              src={row.thumbnail_url}
                              alt=""
                              className="inv-thumb"
                              width={36}
                              height={36}
                            />
                          ) : (
                            <div className="inv-thumb inv-thumb-empty" />
                          )}
                          <div>
                            <div>{row.product_name || "—"}</div>
                            <div className="muted-line" style={{ fontSize: "0.75rem" }}>
                              {row.daraz_item_id || ""}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>{row.store_display_name || row.store_id || "—"}</td>
                      <td>
                        <div>{row.seller_sku || "—"}</div>
                        {row.variant_label ? (
                          <div className="muted-line" style={{ fontSize: "0.75rem" }}>
                            {row.variant_label}
                          </div>
                        ) : null}
                      </td>
                      <td>{formatPrice(row.price)}</td>
                      <td>
                        <div className="row" style={{ gap: "0.4rem", alignItems: "center" }}>
                          <span>{formatStockQuantity(row.quantity)}</span>
                          <StatusBadge tone={stockBadgeTone(badge)}>{badge}</StatusBadge>
                        </div>
                      </td>
                      <td>{row.listing_status || "—"}</td>
                      <td>{formatWhen(row.last_synced)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ justifyContent: "space-between", marginTop: "0.85rem" }}>
            <span className="muted-line">
              {total} SKU{total === 1 ? "" : "s"} · page {page} of {totalPages}
            </span>
            <div className="row" style={{ gap: "0.5rem" }}>
              <button
                type="button"
                className="btn"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
