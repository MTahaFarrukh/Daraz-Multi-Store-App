import { useMemo, useState } from "react";
import { Api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { useStores } from "@/hooks/queries/useStores";
import { useStoreGroups } from "@/hooks/queries/useStoreGroups";
import {
  pollPrintJob,
  useOrderDetail,
  useOrderStatusCounts,
  usePrintOrdersByIds,
  useSyncOrders,
  useUnifiedOrders,
  useValidatePrint,
} from "@/hooks/queries/useUnifiedOrders";
import { Dialog } from "@/components/ui/Dialog";
import {
  EmptyState,
  ErrorBanner,
  LoadingState,
  PageHeader,
  StatusBadge,
  SuccessBanner,
} from "@/components/ui/Primitives";
import type { PrintStateFilter, PrintValidateResponse, UnifiedOrder } from "@/types/api";

const STATUS_TABS: Array<{ key: string; label: string }> = [
  { key: "", label: "All" },
  { key: "pending", label: "Pending" },
  { key: "ready_to_ship", label: "Ready to Ship" },
  { key: "shipped", label: "Shipped" },
  { key: "delivered", label: "Delivered" },
  { key: "canceled", label: "Cancelled" },
  { key: "returned", label: "Returned" },
];

const PAGE_SIZE = 50;

function customerName(o: UnifiedOrder): string {
  const parts = [o.customer_first_name, o.customer_last_name].filter(Boolean);
  return parts.length ? parts.join(" ") : "—";
}

function formatAmount(o: UnifiedOrder): string {
  if (o.price == null || o.price === "") return "—";
  const cur = o.currency || "";
  return `${cur ? `${cur} ` : ""}${o.price}`;
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusTone(group?: string | null): "ok" | "warn" | "danger" | "muted" {
  switch (group) {
    case "ready_to_ship":
      return "ok";
    case "pending":
      return "warn";
    case "canceled":
    case "returned":
      return "danger";
    case "shipped":
    case "delivered":
      return "ok";
    default:
      return "muted";
  }
}

function statusLabel(group?: string | null): string {
  if (!group) return "—";
  const map: Record<string, string> = {
    pending: "Pending",
    ready_to_ship: "Ready to Ship",
    shipped: "Shipped",
    delivered: "Delivered",
    canceled: "Cancelled",
    returned: "Returned",
    other: "Other",
  };
  return map[group] || group;
}

function labelStatus(o: UnifiedOrder): { text: string; tone: "ok" | "warn" | "danger" | "muted" } {
  if (o.has_print) {
    return {
      text: o.print_count && o.print_count > 1 ? `Printed ×${o.print_count}` : "Printed",
      tone: "warn",
    };
  }
  if (o.status_group === "ready_to_ship") {
    return { text: "Unprinted", tone: "ok" };
  }
  return { text: "—", tone: "muted" };
}

export function OrdersPage() {
  const { me } = useAuth();
  const workspaceId = me?.workspace?.id;
  const storesQuery = useStores(workspaceId);
  const groupsQuery = useStoreGroups(workspaceId);
  const stores = storesQuery.data || [];
  const groups = groupsQuery.data || [];

  const [storeScope, setStoreScope] = useState<"all" | string>("all");
  const [groupId, setGroupId] = useState("");
  const [statusGroup, setStatusGroup] = useState("");
  const [printState, setPrintState] = useState<PrintStateFilter>("any");
  const [search, setSearch] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [detailId, setDetailId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState("");

  const [hitlOpen, setHitlOpen] = useState(false);
  const [hitlValidation, setHitlValidation] = useState<PrintValidateResponse | null>(null);
  const [pendingPrintIds, setPendingPrintIds] = useState<string[]>([]);

  const storeFilterParams = useMemo(() => {
    if (groupId) return { group_id: groupId };
    if (storeScope !== "all") return { stores: storeScope };
    return {};
  }, [groupId, storeScope]);

  const countStoreFilter = useMemo(() => {
    if (groupId) {
      const g = groups.find((x) => x.id === groupId);
      const slugs = (g?.store_ids || []).filter(Boolean);
      return slugs.length ? { stores: slugs.join(",") } : {};
    }
    if (storeScope !== "all") return { stores: storeScope };
    return {};
  }, [groupId, storeScope, groups]);

  const listFilters = useMemo(
    () => ({
      ...storeFilterParams,
      status_group: statusGroup || undefined,
      search: search || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      print_state:
        statusGroup === "ready_to_ship" && printState !== "any" ? printState : undefined,
      page,
      page_size: PAGE_SIZE,
      sort: "created_at_daraz_desc",
    }),
    [storeFilterParams, statusGroup, search, dateFrom, dateTo, printState, page]
  );

  const ordersQuery = useUnifiedOrders(workspaceId, listFilters);
  const countsQuery = useOrderStatusCounts(workspaceId, countStoreFilter);
  const detailQuery = useOrderDetail(workspaceId, detailId, Boolean(detailId));
  const syncMutation = useSyncOrders(workspaceId);
  const validateMutation = useValidatePrint(workspaceId);
  const printMutation = usePrintOrdersByIds(workspaceId);

  const orders = ordersQuery.data?.orders || ordersQuery.data?.items || [];
  const total = ordersQuery.data?.total ?? ordersQuery.data?.count ?? 0;
  const pageSize = ordersQuery.data?.page_size ?? PAGE_SIZE;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const counts = countsQuery.data?.counts || {};
  const totalAll = Object.values(counts).reduce((a, b) => a + (Number(b) || 0), 0);

  const hasSyncedData = totalAll > 0 || total > 0;

  function resetSelection() {
    setSelectedIds(new Set());
  }

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAllVisible() {
    const ids = orders.map((o) => o.id);
    const allSelected = ids.length > 0 && ids.every((id) => selectedIds.has(id));
    if (allSelected) {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      });
    } else {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.add(id));
        return next;
      });
    }
  }

  async function handleSync() {
    setError("");
    setOk("");
    setBusy("Syncing orders from Daraz…");
    try {
      const storeIds =
        storeScope === "all"
          ? undefined
          : stores.filter((s) => s.store_id === storeScope).map((s) => s.store_id);
      const result = await syncMutation.mutateAsync(
        storeIds?.length ? { store_ids: storeIds } : undefined
      );
      setOk(
        `Synced ${result.ok}/${result.stores} store(s)` +
          (result.failed ? ` · ${result.failed} failed` : "")
      );
      resetSelection();
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setBusy("");
    }
  }

  async function runPrint(orderIds: string[], allowReprint: boolean) {
    setBusy(`Printing ${orderIds.length} label(s)…`);
    setError("");
    setOk("");
    try {
      const started = await printMutation.mutateAsync({
        orderIds,
        allowReprint,
      });
      const jobId = started.job_id;
      if (!jobId) throw new Error("Print job did not return a job_id");
      const status = await pollPrintJob(jobId, (msg) => setBusy(msg));
      if (status.status === "error") {
        throw new Error(status.error || status.message || "Print failed");
      }
      setOk(`PDF ready · ${status.pages ?? "?"} page(s)`);
      try {
        await Api.downloadPrint(jobId);
      } catch {
        /* manual retry via history */
      }
      resetSelection();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Print failed");
    } finally {
      setBusy("");
    }
  }

  async function handlePrintIds(ids: string[]) {
    if (!ids.length) {
      setError("Select at least one order");
      return;
    }
    setError("");
    setOk("");
    setBusy("Validating print targets…");
    try {
      const validation = await validateMutation.mutateAsync(ids);
      setBusy("");
      const printable = [
        ...validation.new_printable.map((x) => x.order_id),
        ...validation.already_printed.map((x) => x.order_id),
      ];
      if (!printable.length) {
        const reasons = [
          validation.not_eligible.length
            ? `${validation.not_eligible.length} not eligible`
            : "",
          validation.errors.length ? `${validation.errors.length} not found` : "",
        ]
          .filter(Boolean)
          .join(", ");
        setError(reasons ? `Nothing to print (${reasons})` : "Nothing to print");
        return;
      }
      if (validation.already_printed.length) {
        setHitlValidation(validation);
        setPendingPrintIds(printable);
        setHitlOpen(true);
        return;
      }
      // Prefer unprinted only when selection has no already-printed
      await runPrint(
        validation.new_printable.map((x) => x.order_id),
        false
      );
    } catch (err) {
      setBusy("");
      setError(err instanceof Error ? err.message : "Validation failed");
    }
  }

  async function handlePrintSelected() {
    await handlePrintIds(Array.from(selectedIds));
  }

  async function confirmReprint(includePrinted: boolean) {
    setHitlOpen(false);
    if (!hitlValidation) return;
    const ids = includePrinted
      ? pendingPrintIds
      : hitlValidation.new_printable.map((x) => x.order_id);
    if (!ids.length) {
      setError("No unprinted orders in this selection");
      return;
    }
    await runPrint(ids, includePrinted);
    setHitlValidation(null);
    setPendingPrintIds([]);
  }

  const allVisibleSelected =
    orders.length > 0 && orders.every((o) => selectedIds.has(o.id));

  const metaLoading = storesQuery.isLoading || groupsQuery.isLoading;
  const listLoading = ordersQuery.isLoading && !ordersQuery.data;
  const listFetching = ordersQuery.isFetching && !listLoading;

  return (
    <div className="stack orders-page">
      <PageHeader
        title="Orders"
        description="Unified cross-store orders from your local sync. Sync pulls from Daraz; the table reads the local cache."
        actions={
          <>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={listFetching || Boolean(busy)}
              onClick={() => ordersQuery.refetch()}
              title="Refresh table from local DB (does not call Daraz)"
            >
              Refresh
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={syncMutation.isPending || Boolean(busy)}
              onClick={handleSync}
            >
              Sync Orders
            </button>
          </>
        }
      />

      <ErrorBanner message={error} />
      <SuccessBanner message={ok} />
      {busy ? <div className="banner banner-info">{busy}</div> : null}

      <section className="card orders-filters">
        <div className="toolbar orders-filter-row">
          <label className="field" style={{ margin: 0, minWidth: 160 }}>
            <span>Store</span>
            <select
              value={groupId ? `__group__:${groupId}` : storeScope}
              onChange={(e) => {
                const v = e.target.value;
                setPage(1);
                resetSelection();
                if (v.startsWith("__group__:")) {
                  setGroupId(v.slice("__group__:".length));
                  setStoreScope("all");
                  return;
                }
                setGroupId("");
                setStoreScope(v);
              }}
            >
              <option value="all">All stores</option>
              {groups.map((g) => (
                <option key={g.id} value={`__group__:${g.id}`}>
                  Group · {g.name}
                </option>
              ))}
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.display_name || s.store_name || s.store_id}
                </option>
              ))}
            </select>
          </label>

          <label className="field" style={{ margin: 0, minWidth: 200, flex: 1 }}>
            <span>Search</span>
            <input
              type="search"
              placeholder="Order #, customer…"
              value={searchDraft}
              onChange={(e) => setSearchDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  setSearch(searchDraft.trim());
                  setPage(1);
                  resetSelection();
                }
              }}
            />
          </label>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setSearch(searchDraft.trim());
              setPage(1);
              resetSelection();
            }}
          >
            Search
          </button>

          <label className="field" style={{ margin: 0, minWidth: 140 }}>
            <span>From</span>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => {
                setDateFrom(e.target.value);
                setPage(1);
                resetSelection();
              }}
            />
          </label>
          <label className="field" style={{ margin: 0, minWidth: 140 }}>
            <span>To</span>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => {
                setDateTo(e.target.value);
                setPage(1);
                resetSelection();
              }}
            />
          </label>
        </div>

        <div className="orders-status-tabs" role="tablist" aria-label="Status">
          {STATUS_TABS.map((tab) => {
            const count =
              tab.key === "" ? totalAll : Number(counts[tab.key] ?? 0);
            const active = statusGroup === tab.key;
            return (
              <button
                key={tab.key || "all"}
                type="button"
                role="tab"
                aria-selected={active}
                className={`orders-status-tab${active ? " active" : ""}`}
                onClick={() => {
                  setStatusGroup(tab.key);
                  if (tab.key !== "ready_to_ship") setPrintState("any");
                  setPage(1);
                  resetSelection();
                }}
              >
                <span>{tab.label}</span>
                <span className="orders-status-count">{count}</span>
              </button>
            );
          })}
        </div>

        {statusGroup === "ready_to_ship" ? (
          <div className="toolbar" style={{ marginBottom: 0 }}>
            <span className="orders-print-label">Label</span>
            {(
              [
                ["any", "All RTS"],
                ["unprinted", "Unprinted"],
                ["printed", "Printed"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`btn btn-sm ${printState === value ? "btn-primary" : "btn-ghost"}`}
                onClick={() => {
                  setPrintState(value);
                  setPage(1);
                  resetSelection();
                }}
              >
                {label}
              </button>
            ))}
          </div>
        ) : null}
      </section>

      {metaLoading || listLoading ? (
        <LoadingState label="Loading orders…" />
      ) : !hasSyncedData && !search && !statusGroup && !dateFrom && !dateTo ? (
        <EmptyState
          title="No synced orders yet"
          description="Pull recent orders from Daraz into the local cache, then filter and print labels here."
          action={
            <button
              type="button"
              className="btn btn-primary"
              disabled={syncMutation.isPending}
              onClick={handleSync}
            >
              Sync Orders
            </button>
          }
        />
      ) : orders.length === 0 ? (
        <EmptyState
          title="No matching orders"
          description="Try another status, store, print state, or clear search/date filters. Sync again if you expect newer Daraz data."
          action={
            <button type="button" className="btn btn-ghost" onClick={handleSync}>
              Sync Orders
            </button>
          }
        />
      ) : (
        <>
          <div className={`orders-table-wrap${listFetching ? " is-fetching" : ""}`}>
            <div className="table-wrap orders-table-desktop">
              <table className="data">
                <thead>
                  <tr>
                    <th className="col-check">
                      <input
                        type="checkbox"
                        checked={allVisibleSelected}
                        onChange={toggleSelectAllVisible}
                        aria-label="Select all on page"
                      />
                    </th>
                    <th>Order #</th>
                    <th>Store</th>
                    <th>Customer</th>
                    <th>Items</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th>Label</th>
                    <th>Date</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o) => {
                    const label = labelStatus(o);
                    return (
                      <tr
                        key={o.id}
                        className={selectedIds.has(o.id) ? "row-selected" : undefined}
                        onClick={() => setDetailId(o.id)}
                        style={{ cursor: "pointer" }}
                      >
                        <td
                          className="col-check"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            checked={selectedIds.has(o.id)}
                            onChange={() => toggleSelect(o.id)}
                            aria-label={`Select order ${o.order_number || o.daraz_order_id}`}
                          />
                        </td>
                        <td>
                          <strong>{String(o.order_number || o.daraz_order_id)}</strong>
                        </td>
                        <td>{o.store_display_name || o.store_slug || "—"}</td>
                        <td>{customerName(o)}</td>
                        <td>{o.items_count ?? "—"}</td>
                        <td>{formatAmount(o)}</td>
                        <td>
                          <StatusBadge tone={statusTone(o.status_group)}>
                            {statusLabel(o.status_group)}
                          </StatusBadge>
                        </td>
                        <td>
                          <StatusBadge tone={label.tone}>{label.text}</StatusBadge>
                        </td>
                        <td>{formatDate(o.created_at_daraz)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="orders-mobile-list">
              {orders.map((o) => {
                const label = labelStatus(o);
                return (
                  <article
                    key={o.id}
                    className={`orders-mobile-card${selectedIds.has(o.id) ? " selected" : ""}`}
                  >
                    <div className="orders-mobile-top">
                      <label className="orders-mobile-check" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selectedIds.has(o.id)}
                          onChange={() => toggleSelect(o.id)}
                        />
                      </label>
                      <button
                        type="button"
                        className="orders-mobile-main"
                        onClick={() => setDetailId(o.id)}
                      >
                        <strong>{String(o.order_number || o.daraz_order_id)}</strong>
                        <span className="muted-line">
                          {o.store_display_name || o.store_slug || "—"}
                        </span>
                      </button>
                      <StatusBadge tone={statusTone(o.status_group)}>
                        {statusLabel(o.status_group)}
                      </StatusBadge>
                    </div>
                    <div className="orders-mobile-meta">
                      <span>{customerName(o)}</span>
                      <span>{formatAmount(o)}</span>
                      <StatusBadge tone={label.tone}>{label.text}</StatusBadge>
                    </div>
                    <div className="muted-line">{formatDate(o.created_at_daraz)}</div>
                  </article>
                );
              })}
            </div>
          </div>

          <div className="orders-pagination">
            <span className="muted-line">
              {total} order{total === 1 ? "" : "s"}
              {listFetching ? " · updating…" : ""}
            </span>
            <div className="row">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={page <= 1 || Boolean(busy)}
                onClick={() => {
                  setPage((p) => Math.max(1, p - 1));
                  resetSelection();
                }}
              >
                Previous
              </button>
              <span className="orders-page-indicator">
                Page {page} / {totalPages}
              </span>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={page >= totalPages || Boolean(busy)}
                onClick={() => {
                  setPage((p) => p + 1);
                  resetSelection();
                }}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}

      {selectedIds.size > 0 ? (
        <div className="orders-bulk-bar" role="region" aria-label="Bulk actions">
          <span>
            <strong>{selectedIds.size}</strong> selected
          </span>
          <div className="row">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={resetSelection}
            >
              Clear
            </button>
            <button
              type="button"
              className="btn btn-accent"
              disabled={Boolean(busy) || validateMutation.isPending}
              onClick={handlePrintSelected}
            >
              Print Labels
            </button>
          </div>
        </div>
      ) : null}

      {detailId ? (
        <div
          className="orders-drawer-backdrop"
          role="presentation"
          onClick={() => setDetailId(null)}
        >
          <aside
            className="orders-drawer"
            role="dialog"
            aria-label="Order detail"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="orders-drawer-header">
              <h3>Order detail</h3>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setDetailId(null)}
              >
                Close
              </button>
            </div>
            <div className="orders-drawer-body">
              {detailQuery.isLoading ? (
                <LoadingState label="Loading detail…" />
              ) : detailQuery.isError ? (
                <ErrorBanner
                  message={
                    detailQuery.error instanceof Error
                      ? detailQuery.error.message
                      : "Failed to load order"
                  }
                />
              ) : detailQuery.data ? (
                <>
                  <div className="orders-detail-block">
                    <p className="label">Order</p>
                    <p className="value">
                      {String(
                        detailQuery.data.order.order_number ||
                          detailQuery.data.order.daraz_order_id
                      )}
                    </p>
                    <p className="muted-line">
                      {detailQuery.data.order.store_display_name ||
                        detailQuery.data.order.store_slug ||
                        "—"}
                    </p>
                  </div>
                  <div className="row" style={{ gap: "0.5rem" }}>
                    <StatusBadge tone={statusTone(detailQuery.data.order.status_group)}>
                      {statusLabel(detailQuery.data.order.status_group)}
                    </StatusBadge>
                    <StatusBadge tone={labelStatus(detailQuery.data.order).tone}>
                      {labelStatus(detailQuery.data.order).text}
                    </StatusBadge>
                  </div>
                  <div className="orders-detail-grid">
                    <div>
                      <p className="label">Customer</p>
                      <p>{customerName(detailQuery.data.order)}</p>
                    </div>
                    <div>
                      <p className="label">Amount</p>
                      <p>{formatAmount(detailQuery.data.order)}</p>
                    </div>
                    <div>
                      <p className="label">Items</p>
                      <p>{detailQuery.data.order.items_count ?? "—"}</p>
                    </div>
                    <div>
                      <p className="label">Created</p>
                      <p>{formatDate(detailQuery.data.order.created_at_daraz)}</p>
                    </div>
                  </div>
                  {detailQuery.data.items?.length ? (
                    <div>
                      <h4 className="section-title">Line items</h4>
                      <ul className="orders-item-list">
                        {detailQuery.data.items.map((item) => (
                          <li key={item.id || item.daraz_order_item_id}>
                            <strong>{item.name || item.sku || "Item"}</strong>
                            <span className="muted-line">
                              Qty {item.quantity ?? 1}
                              {item.status_raw ? ` · ${item.status_raw}` : ""}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {detailQuery.data.print_history?.length ? (
                    <div>
                      <h4 className="section-title">Print history</h4>
                      <ul className="orders-item-list">
                        {detailQuery.data.print_history.map((p) => (
                          <li key={p.id}>
                            <strong>{formatDate(p.printed_at)}</strong>
                            <span className="muted-line">
                              {p.is_reprint ? "Reprint" : "First print"}
                              {p.fetch_source ? ` · ${p.fetch_source}` : ""}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <button
                    type="button"
                    className="btn btn-accent"
                    disabled={Boolean(busy)}
                    onClick={() => {
                      const id = detailQuery.data!.order.id;
                      setDetailId(null);
                      void handlePrintIds([id]);
                    }}
                  >
                    Print this label
                  </button>
                </>
              ) : null}
            </div>
          </aside>
        </div>
      ) : null}

      <Dialog
        open={hitlOpen}
        title="Already printed labels"
        onClose={() => {
          setHitlOpen(false);
          setHitlValidation(null);
        }}
      >
        <p style={{ margin: 0, color: "var(--muted)" }}>
          {hitlValidation?.already_printed.length || 0} order(s) already have a printed
          label. Reprinting can create duplicates on the warehouse floor.
        </p>
        {hitlValidation?.new_printable.length ? (
          <p style={{ margin: 0 }}>
            {hitlValidation.new_printable.length} unprinted order(s) can print without a
            reprint.
          </p>
        ) : (
          <p style={{ margin: 0 }}>No unprinted orders in this selection.</p>
        )}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              setHitlOpen(false);
              setHitlValidation(null);
            }}
          >
            Cancel
          </button>
          {hitlValidation?.new_printable.length ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void confirmReprint(false)}
            >
              Print unprinted only
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-accent"
            onClick={() => void confirmReprint(true)}
          >
            Print including reprints
          </button>
        </div>
      </Dialog>
    </div>
  );
}
