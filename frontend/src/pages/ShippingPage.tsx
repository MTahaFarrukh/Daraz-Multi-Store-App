import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { useStores } from "@/hooks/queries/useStores";
import { useStoreGroups } from "@/hooks/queries/useStoreGroups";
import { usePrintJobs } from "@/hooks/queries/usePrintAndOrders";
import {
  pollPrintJob,
  usePrintOrdersByIds,
} from "@/hooks/queries/useUnifiedOrders";
import { RtsSelectionToolbar } from "@/components/orders/RtsSelectionToolbar";
import { StoreSelector } from "@/components/stores/StoreSelector";
import { Dialog } from "@/components/ui/Dialog";
import {
  EmptyState,
  ErrorBanner,
  PageHeader,
  StatusBadge,
  SuccessBanner,
} from "@/components/ui/Primitives";
import {
  formatPrintTime,
  partitionPrintSelection,
  resolvePrintLabelStatus,
  selectAllIds,
  selectUnprintedIds,
} from "@/lib/printLabelStatus";
import type { UnifiedOrder } from "@/types/api";

const SELECTION_KEY = "multistore_shipping_selection_v1";

type StoreRtsStatus = {
  store_id?: string;
  display_name?: string;
  ok?: boolean;
  incomplete?: boolean;
  error?: string | null;
  unique?: number;
  countTotal?: number | null;
  elapsed_ms?: number;
};

export function ShippingPage() {
  const { me } = useAuth();
  const workspaceId = me?.workspace?.id;
  const [tab, setTab] = useState<"rts" | "history">("rts");
  const storesQuery = useStores(workspaceId);
  const groupsQuery = useStoreGroups(workspaceId);
  const stores = storesQuery.data || [];
  const groups = groupsQuery.data || [];

  const [selectedStores, setSelectedStores] = useState<string[]>([]);
  const [selectionReady, setSelectionReady] = useState(false);
  const [groupId, setGroupId] = useState("");
  const [rtsEnabled, setRtsEnabled] = useState(false);
  const [orderSelected, setOrderSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState("");
  const [hitlOpen, setHitlOpen] = useState(false);
  const [pendingAllIds, setPendingAllIds] = useState<string[]>([]);
  const [pendingUnprintedIds, setPendingUnprintedIds] = useState<string[]>([]);
  const [rtsOrders, setRtsOrders] = useState<UnifiedOrder[]>([]);
  const [storeStatuses, setStoreStatuses] = useState<StoreRtsStatus[]>([]);
  const [partialLoad, setPartialLoad] = useState(false);
  const [loadElapsedMs, setLoadElapsedMs] = useState<number | null>(null);

  const [failedPrintIds, setFailedPrintIds] = useState<string[]>([]);
  const [printFailures, setPrintFailures] = useState<
    Array<{ store?: string; order_number?: string; reason?: string }>
  >([]);
  const [lastPrintSummary, setLastPrintSummary] = useState("");
  const [hitlPrintedCount, setHitlPrintedCount] = useState(0);

  const printMutation = usePrintOrdersByIds(workspaceId);
  const printJobsQuery = usePrintJobs(workspaceId, true);

  const orders: UnifiedOrder[] = rtsEnabled ? rtsOrders : [];

  const history = printJobsQuery.data || [];
  const historyNote =
    printJobsQuery.isError
      ? printJobsQuery.error instanceof Error
        ? printJobsQuery.error.message
        : "Print history list is not available."
      : printJobsQuery.isSuccess && history.length === 0
        ? "No print jobs recorded for this workspace yet."
        : "";

  const lastBatchLabel = useMemo(() => {
    const done = history
      .filter((j) => j.status === "done" && (j.updated_at || j.started_at))
      .sort((a, b) =>
        String(b.updated_at || b.started_at || "").localeCompare(
          String(a.updated_at || a.started_at || "")
        )
      );
    return formatPrintTime(done[0]?.updated_at || done[0]?.started_at) || null;
  }, [history]);

  const unprintedCount = useMemo(
    () => orders.filter((o) => !o.has_print && !(Number(o.print_count) > 0)).length,
    [orders]
  );

  useEffect(() => {
    if (!storesQuery.isSuccess || selectionReady) return;
    const valid = new Set(stores.map((x) => x.store_id));
    const saved = localStorage.getItem(SELECTION_KEY);
    if (saved) {
      try {
        const ids = (JSON.parse(saved) as string[]).filter((id) => valid.has(id));
        setSelectedStores(ids);
        setSelectionReady(true);
        return;
      } catch {
        /* fall through */
      }
    }
    setSelectedStores(stores.map((x) => x.store_id));
    setSelectionReady(true);
  }, [storesQuery.isSuccess, stores, selectionReady]);

  useEffect(() => {
    if (!selectionReady) return;
    localStorage.setItem(SELECTION_KEY, JSON.stringify(selectedStores));
  }, [selectedStores, selectionReady]);

  useEffect(() => {
    setRtsEnabled(false);
    setRtsOrders([]);
    setStoreStatuses([]);
    setPartialLoad(false);
    setOrderSelected(new Set());
  }, [selectedStores]);

  useEffect(() => {
    const metaErr =
      (storesQuery.error instanceof Error && storesQuery.error.message) ||
      (groupsQuery.error instanceof Error && groupsQuery.error.message) ||
      "";
    if (metaErr) setError(metaErr);
  }, [storesQuery.error, groupsQuery.error]);

  async function loadRts(retryStoreId?: string) {
    setError("");
    setOk("");
    const targets = retryStoreId ? [retryStoreId] : selectedStores;
    if (!targets.length) {
      setError("Select at least one store");
      return;
    }
    setBusy("Loading current RTS from Daraz…");
    setRtsEnabled(true);
    try {
      const result = await Api.loadShippingRts(targets, true);
      const incoming = result.orders || [];

      if (retryStoreId) {
        // Merge: replace that store's rows, keep others
        setRtsOrders((prev) => {
          const kept = prev.filter(
            (o) => (o.store_slug || o.store_id) !== retryStoreId
          );
          return [...kept, ...incoming];
        });
        setStoreStatuses((prev) => {
          const others = prev.filter((s) => s.store_id !== retryStoreId);
          return [...others, ...(result.stores || [])];
        });
      } else {
        setRtsOrders(incoming);
        setStoreStatuses(result.stores || []);
      }

      setPartialLoad(Boolean(result.partial));
      setLoadElapsedMs(result.elapsed_ms ?? null);
      setOrderSelected(new Set());

      const lines = (result.stores || []).map((s) => {
        const name = s.display_name || s.store_id || "?";
        if (!s.ok) return `${name}: failed${s.error ? ` (${s.error})` : ""}`;
        return `${name}: ${s.unique ?? 0} RTS · ${s.elapsed_ms ?? "?"}ms`;
      });
      const summary =
        `RTS loaded · ${result.count} order(s) · ${result.unprinted_count} unprinted` +
        (result.partial ? " · PARTIAL — some stores failed" : "") +
        (result.elapsed_ms != null ? ` · ${result.elapsed_ms}ms` : "") +
        (lines.length ? ` · ${lines.join(" · ")}` : "");
      setOk(summary);
      if (result.partial) {
        setError(
          `Incomplete RTS batch: ${result.stores_failed}/${result.stores_requested} store(s) failed. Successful stores are still shown.`
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load RTS");
    } finally {
      setBusy("");
    }
  }

  function toggleOrder(id: string) {
    setOrderSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function runPrint(orderIds: string[], allowReprint: boolean) {
    setBusy("Starting print job…");
    setError("");
    setOk("");
    setFailedPrintIds([]);
    setPrintFailures([]);
    setLastPrintSummary("");
    try {
      const started = await printMutation.mutateAsync({ orderIds, allowReprint });
      const jobId = started.job_id;
      if (!jobId) throw new Error("Print job did not return a job_id");
      // Job is queued — leave "Starting…" immediately (Daraz work is background)
      setBusy("Gathering labels…");
      const status = await pollPrintJob(jobId, (msg) => {
        if (msg && msg !== "Starting print job…" && msg !== "Print job queued…") {
          setBusy(msg);
        } else {
          setBusy("Gathering labels…");
        }
      });
      if (status.status === "error") {
        throw new Error(status.error || status.message || "Print failed");
      }
      const result = (status.result || status) as Record<string, any>;
      const summary = (result.summary || {}) as Record<string, any>;
      const message =
        result.message ||
        summary.message ||
        `PDF ready · ${status.pages ?? result.pages ?? "?"} page(s)`;
      const failedIds: string[] = result.failed_order_ids || summary.failed_order_ids || [];
      const printStatus = result.print_status || summary.print_status;
      const outcomes: Array<Record<string, any>> = Array.isArray(result.outcomes)
        ? result.outcomes
        : Array.isArray(result.failures)
          ? result.failures
          : [];
      const failureRows = outcomes
        .filter((o) => o.state !== "SUCCESS" && o.success !== true)
        .map((o) => ({
          store: String(o.store_display_name || o.store_slug || o.store_id || ""),
          order_number: String(o.order_number || o.daraz_order_id || o.order_id || ""),
          reason: String(o.reason || o.state || "failed"),
        }));
      if (!failureRows.length && failedIds.length) {
        for (const id of failedIds) {
          const ord = orders.find((o) => o.id === id);
          failureRows.push({
            store: String(ord?.store_display_name || ord?.store_slug || ""),
            order_number: String(ord?.order_number || ord?.daraz_order_id || id),
            reason: "failed",
          });
        }
      }
      setLastPrintSummary(message);
      setFailedPrintIds(failedIds);
      setPrintFailures(failureRows);
      if (printStatus === "partial_success" || failedIds.length) {
        setOk(message);
        setError(
          `${failedIds.length} label(s) need attention. Use Retry Failed to reprint only those.`
        );
      } else {
        setOk(message);
      }
      try {
        await Api.downloadPrint(jobId);
      } catch {
        /* retry via history */
      }
      setOrderSelected(new Set());
      await loadRts();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Print failed");
    } finally {
      setBusy("");
    }
  }

  async function handlePrintSelected() {
    const ids = Array.from(orderSelected).filter(Boolean);
    if (!ids.length) {
      setError("Select at least one order");
      return;
    }
    if (ids.some((id) => !id || id === "null" || id === "undefined")) {
      setError("Some RTS rows are missing local ids — reload RTS and try again");
      return;
    }
    const visibleIds = new Set(orders.map((o) => o.id).filter(Boolean));
    if (ids.some((id) => !visibleIds.has(id))) {
      setError("Selection includes orders not in the loaded RTS list — reload RTS");
      return;
    }
    // Client-only HITL from loaded RTS print events — no backend validate/hydrate.
    const part = partitionPrintSelection(orders, ids);
    if (!part.selected.length) {
      setError("Nothing to print");
      return;
    }
    if (part.printed.length) {
      setPendingAllIds(part.selected.map((o) => o.id));
      setPendingUnprintedIds(part.unprinted.map((o) => o.id));
      setHitlPrintedCount(part.printed.length);
      setHitlOpen(true);
      return;
    }
    await runPrint(
      part.selected.map((o) => o.id),
      false
    );
  }

  async function confirmPrintSelected() {
    setHitlOpen(false);
    const ids = pendingAllIds;
    if (!ids.length) {
      setError("Nothing to print");
      return;
    }
    await runPrint(ids, true);
    setPendingAllIds([]);
    setPendingUnprintedIds([]);
    setHitlPrintedCount(0);
  }

  async function confirmPrintUnprintedOnly() {
    setHitlOpen(false);
    const ids = pendingUnprintedIds;
    if (!ids.length) {
      setError("No unprinted orders in this selection");
      return;
    }
    await runPrint(ids, false);
    setPendingAllIds([]);
    setPendingUnprintedIds([]);
    setHitlPrintedCount(0);
  }

  return (
    <div className="stack">
      <PageHeader
        title="Shipping"
        description="Select stores → Load RTS → Select Unprinted → Print Labels. RTS comes live from Daraz; print badges come only from MultiStore print events."
      />

      <div className="tabs" role="tablist">
        <button
          type="button"
          className={`tab${tab === "rts" ? " active" : ""}`}
          onClick={() => setTab("rts")}
        >
          Ready to Ship
        </button>
        <button
          type="button"
          className={`tab${tab === "history" ? " active" : ""}`}
          onClick={() => setTab("history")}
        >
          Print History
        </button>
      </div>

      <ErrorBanner message={error} />
      <SuccessBanner message={ok} />
      {busy ? <div className="banner banner-info">{busy}</div> : null}

      {tab === "rts" ? (
        <>
          <section className="card">
            <h3 className="section-title">Stores</h3>
            <StoreSelector
              stores={stores}
              selected={selectedStores}
              onChange={(ids) => {
                setSelectedStores(ids);
                setGroupId("");
              }}
              groups={groups}
              activeGroupId={groupId}
              onGroupChange={(id) => {
                setGroupId(id);
                if (!id) return;
                if (id === "__all__") {
                  setSelectedStores(stores.map((x) => x.store_id));
                  return;
                }
                const g = groups.find((x) => x.id === id);
                if (!g) {
                  setSelectedStores([]);
                  return;
                }
                const valid = new Set(stores.map((x) => x.store_id));
                setSelectedStores(g.store_ids.filter((sid) => valid.has(sid)));
              }}
            />
            <p className="muted-line" style={{ marginTop: "0.65rem" }}>
              Load RTS queries Daraz live ready_to_ship for the selected stores and reconciles
              UNPRINTED / Printed / Reprinted from local print events. Empty selection never means
              all stores.{" "}
              <Link to="/app/orders" style={{ fontWeight: 700, color: "var(--teal-deep)" }}>
                Open Orders →
              </Link>
            </p>
            <div className="row" style={{ marginTop: "0.75rem" }}>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!selectedStores.length || Boolean(busy)}
                onClick={() => loadRts()}
                data-testid="shipping-load-rts"
              >
                Load RTS
              </button>
              <button
                type="button"
                className="btn btn-accent"
                disabled={!orderSelected.size || Boolean(busy)}
                onClick={handlePrintSelected}
              >
                Print Labels
              </button>
              {failedPrintIds.length ? (
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={Boolean(busy)}
                  onClick={() => runPrint(failedPrintIds, false)}
                >
                  Retry Failed {failedPrintIds.length}
                </button>
              ) : null}
            </div>
            {lastPrintSummary ? (
              <p className="muted-line" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
                Last print: {lastPrintSummary}
              </p>
            ) : null}
            {printFailures.length ? (
              <ul style={{ marginTop: "0.5rem", marginBottom: 0, paddingLeft: "1.1rem" }}>
                {printFailures.map((f, i) => (
                  <li key={`${f.order_number}-${i}`}>
                    {f.store ? `${f.store} · ` : ""}
                    {f.order_number || "—"}
                    {f.reason ? ` — ${f.reason}` : ""}
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          {rtsEnabled && storeStatuses.length ? (
            <section className="card">
              <h3 className="section-title">Per-store result</h3>
              {partialLoad ? (
                <div className="banner banner-info" style={{ marginBottom: "0.75rem" }}>
                  Partial load — successful stores are listed; failed stores need Retry.
                </div>
              ) : null}
              <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                {storeStatuses.map((s) => (
                  <li key={s.store_id || s.display_name} style={{ marginBottom: "0.35rem" }}>
                    {s.ok ? (
                      <>
                        ✓ <strong>{s.display_name || s.store_id}</strong> — {s.unique ?? 0}{" "}
                        RTS
                        {s.countTotal != null ? ` (Daraz ${s.countTotal})` : ""} ·{" "}
                        {s.elapsed_ms ?? "?"}ms
                      </>
                    ) : (
                      <>
                        ✕ <strong>{s.display_name || s.store_id}</strong> — Failed
                        {s.error ? `: ${s.error}` : ""}{" "}
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={Boolean(busy)}
                          onClick={() => loadRts(s.store_id)}
                        >
                          Retry
                        </button>
                      </>
                    )}
                  </li>
                ))}
              </ul>
              <p className="muted-line" style={{ marginBottom: 0 }}>
                Total shown: {orders.length} · Unprinted: {unprintedCount}
                {loadElapsedMs != null ? ` · ${loadElapsedMs}ms` : ""}
                {lastBatchLabel ? ` · Last print batch ${lastBatchLabel}` : ""}
              </p>
            </section>
          ) : null}

          {!rtsEnabled ? (
            <EmptyState
              title="Load RTS to continue"
              description="Select stores, then Load RTS. Workflow: Select stores → Load RTS → Select Unprinted → Print Labels."
            />
          ) : orders.length === 0 ? (
            <EmptyState
              title="No current RTS"
              description={
                partialLoad
                  ? "No orders from successful stores (or all selected stores failed)."
                  : "Daraz returned no ready_to_ship orders for the selected stores."
              }
            />
          ) : (
            <>
              <RtsSelectionToolbar
                orders={orders}
                selectedCount={orderSelected.size}
                lastBatchLabel={lastBatchLabel}
                onSelectAll={() => setOrderSelected(new Set(selectAllIds(orders)))}
                onSelectUnprinted={() =>
                  setOrderSelected(new Set(selectUnprintedIds(orders)))
                }
                onClear={() => setOrderSelected(new Set())}
                disabled={Boolean(busy)}
              />
              <section className="card" style={{ overflowX: "auto" }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th />
                      <th>Order</th>
                      <th>Store</th>
                      <th>Status</th>
                      <th>Print</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((o) => {
                      const id = String(o.id || "");
                      const label = resolvePrintLabelStatus(o);
                      return (
                        <tr key={`${o.store_slug}-${o.daraz_order_id}-${id}`}>
                          <td>
                            <input
                              type="checkbox"
                              checked={id ? orderSelected.has(id) : false}
                              disabled={!id}
                              onChange={() => id && toggleOrder(id)}
                            />
                          </td>
                          <td>
                            <strong>{o.order_number || o.daraz_order_id}</strong>
                            <div className="muted-line" style={{ fontSize: "0.75rem" }}>
                              {o.daraz_order_id}
                            </div>
                          </td>
                          <td>{o.store_display_name || o.store_slug || "—"}</td>
                          <td>
                            <StatusBadge>{o.status_group || o.status_raw || "RTS"}</StatusBadge>
                          </td>
                          <td>
                            <StatusBadge tone={label.tone}>{label.text}</StatusBadge>
                          </td>
                          <td style={{ whiteSpace: "nowrap", fontSize: "0.8rem" }}>
                            {o.created_at_daraz || "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </section>
            </>
          )}
        </>
      ) : (
        <section className="card">
          <h3 className="section-title">Print History</h3>
          {historyNote ? <p className="muted-line">{historyNote}</p> : null}
          <ul>
            {history.map((j) => (
              <li key={j.id}>
                {j.status} · {formatPrintTime(j.updated_at || j.started_at) || "—"} ·{" "}
                {j.id}
              </li>
            ))}
          </ul>
        </section>
      )}

      <Dialog
        open={hitlOpen}
        title="Print confirmation"
        onClose={() => {
          setHitlOpen(false);
          setPendingAllIds([]);
          setPendingUnprintedIds([]);
          setHitlPrintedCount(0);
        }}
      >
        <p>
          You selected {hitlPrintedCount} label
          {hitlPrintedCount === 1 ? "" : "s"} that were printed before.
        </p>
        <div className="row">
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              setHitlOpen(false);
              setPendingAllIds([]);
              setPendingUnprintedIds([]);
              setHitlPrintedCount(0);
            }}
          >
            Cancel
          </button>
          {pendingUnprintedIds.length ? (
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => void confirmPrintUnprintedOnly()}
            >
              Print {pendingUnprintedIds.length} Unprinted
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void confirmPrintSelected()}
          >
            Print Selected {pendingAllIds.length}
          </button>
        </div>
      </Dialog>
    </div>
  );
}
