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
  useSyncOrders,
  useUnifiedOrders,
  useValidatePrint,
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
  resolvePrintLabelStatus,
  selectAllIds,
  selectUnprintedIds,
} from "@/lib/printLabelStatus";
import type { PrintValidateResponse, UnifiedOrder } from "@/types/api";

const SELECTION_KEY = "multistore_shipping_selection_v1";

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
  const [hitlValidation, setHitlValidation] = useState<PrintValidateResponse | null>(null);
  const [pendingPrintIds, setPendingPrintIds] = useState<string[]>([]);

  const syncMutation = useSyncOrders(workspaceId);
  const validateMutation = useValidatePrint(workspaceId);
  const printMutation = usePrintOrdersByIds(workspaceId);
  const printJobsQuery = usePrintJobs(workspaceId, true);

  const listFilters = useMemo(
    () => ({
      stores: selectedStores.length ? selectedStores.join(",") : undefined,
      status_group: "ready_to_ship" as const,
      print_state: "any" as const,
      page: 1,
      page_size: 100,
      sort: "created_at_daraz_desc",
    }),
    [selectedStores]
  );

  // Only fetch local RTS when enabled AND at least one store selected (empty ≠ all).
  const canLoadRts = rtsEnabled && selectedStores.length > 0;
  const rtsQuery = useUnifiedOrders(workspaceId, listFilters, { enabled: canLoadRts });
  const orders: UnifiedOrder[] = canLoadRts
    ? rtsQuery.data?.orders || rtsQuery.data?.items || []
    : [];

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
    setOrderSelected(new Set());
  }, [selectedStores]);

  useEffect(() => {
    const metaErr =
      (storesQuery.error instanceof Error && storesQuery.error.message) ||
      (groupsQuery.error instanceof Error && groupsQuery.error.message) ||
      "";
    if (metaErr) setError(metaErr);
  }, [storesQuery.error, groupsQuery.error]);

  async function loadRts() {
    setError("");
    setOk("");
    if (!selectedStores.length) {
      setError("Select at least one store");
      return;
    }
    setBusy("Loading RTS from local cache…");
    setRtsEnabled(true);
    try {
      const result = await rtsQuery.refetch();
      if (result.error) throw result.error;
      const rows = result.data?.orders || result.data?.items || [];
      setOk(
        `Loaded ${rows.length} RTS order(s) · Unprinted badges come from MultiStore print events`
      );
      setOrderSelected(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load RTS");
    } finally {
      setBusy("");
    }
  }

  async function syncThenLoad() {
    if (!selectedStores.length) {
      setError("Select at least one store");
      return;
    }
    setError("");
    setOk("");
    setBusy("Syncing from Daraz…");
    try {
      const result = await syncMutation.mutateAsync({ store_ids: selectedStores });
      setOk(
        `Synced ${result.ok}/${result.stores} store(s)` +
          (result.failed ? ` · ${result.failed} failed` : "")
      );
      await loadRts();
    } catch (err) {
      setBusy("");
      setError(err instanceof Error ? err.message : "Sync failed");
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
    setBusy(`Printing ${orderIds.length} label(s)…`);
    setError("");
    setOk("");
    try {
      const started = await printMutation.mutateAsync({ orderIds, allowReprint });
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
        /* retry via history */
      }
      setOrderSelected(new Set());
      await rtsQuery.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Print failed");
    } finally {
      setBusy("");
    }
  }

  async function handlePrintSelected() {
    const ids = Array.from(orderSelected);
    if (!ids.length) {
      setError("Select at least one order");
      return;
    }
    setBusy("Validating print targets…");
    try {
      const validation = await validateMutation.mutateAsync(ids);
      setBusy("");
      const printable = [
        ...validation.new_printable.map((x) => x.order_id),
        ...validation.already_printed.map((x) => x.order_id),
      ];
      if (!printable.length) {
        setError("Nothing to print (not eligible or not found)");
        return;
      }
      if (validation.already_printed.length) {
        setHitlValidation(validation);
        setPendingPrintIds(printable);
        setHitlOpen(true);
        return;
      }
      await runPrint(
        validation.new_printable.map((x) => x.order_id),
        false
      );
    } catch (err) {
      setBusy("");
      setError(err instanceof Error ? err.message : "Validation failed");
    }
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

  return (
    <div className="stack">
      <PageHeader
        title="Shipping"
        description="RTS labels from your local order cache with Unprinted/Printed badges from MultiStore print events. Empty store selection never means all stores."
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
              Sync pulls Daraz into the local cache. Load RTS shows all ready-to-ship
              rows — previously printed stay visible as Printed; new ones show UNPRINTED.{" "}
              <Link to="/app/orders" style={{ fontWeight: 700, color: "var(--teal-deep)" }}>
                Open Orders →
              </Link>
            </p>
            <div className="row" style={{ marginTop: "0.75rem" }}>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={!selectedStores.length || Boolean(busy)}
                onClick={syncThenLoad}
              >
                Sync + Load RTS
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!selectedStores.length || Boolean(busy)}
                onClick={loadRts}
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
            </div>
          </section>

          {!rtsEnabled ? (
            <EmptyState
              title="Load RTS orders"
              description="Select stores, then Sync + Load RTS (or Load RTS if already synced). Empty store selection never means all stores."
            />
          ) : orders.length === 0 ? (
            <EmptyState
              title="No RTS orders in local cache"
              description="Try Sync + Load RTS. If Seller Center shows RTS orders, sync may still be catching up."
              action={
                <button type="button" className="btn btn-primary" onClick={syncThenLoad}>
                  Sync + Load RTS
                </button>
              }
            />
          ) : (
            <section className="card">
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
              <div className="table-wrap" style={{ marginTop: "0.75rem" }}>
                <table className="data">
                  <thead>
                    <tr>
                      <th className="col-check" />
                      <th>Store</th>
                      <th>Order</th>
                      <th>Items</th>
                      <th>Label</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((o) => {
                      const label = resolvePrintLabelStatus(o);
                      return (
                        <tr
                          key={o.id}
                          className={orderSelected.has(o.id) ? "row-selected" : undefined}
                        >
                          <td className="col-check">
                            <input
                              type="checkbox"
                              checked={orderSelected.has(o.id)}
                              onChange={() => toggleOrder(o.id)}
                              aria-label={`Select ${o.order_number || o.daraz_order_id}`}
                            />
                          </td>
                          <td>{o.store_display_name || o.store_slug || "—"}</td>
                          <td>
                            <strong>{String(o.order_number || o.daraz_order_id)}</strong>
                          </td>
                          <td>{o.items_count ?? "—"}</td>
                          <td>
                            <StatusBadge tone={label.tone}>{label.text}</StatusBadge>
                          </td>
                          <td>{o.created_at_daraz || "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      ) : (
        <section className="card">
          <h3 className="section-title">Print history</h3>
          {historyNote ? <p style={{ color: "var(--muted)" }}>{historyNote}</p> : null}
          {history.length > 0 ? (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Status</th>
                    <th>Started</th>
                    <th>Pages</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {history.map((j) => (
                    <tr key={j.id}>
                      <td style={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                        {j.id.slice(0, 8)}…
                      </td>
                      <td>
                        <StatusBadge
                          tone={
                            j.status === "done"
                              ? "ok"
                              : j.status === "error"
                                ? "danger"
                                : "muted"
                          }
                        >
                          {j.status}
                        </StatusBadge>
                      </td>
                      <td>{j.started_at || j.updated_at || "—"}</td>
                      <td>{j.pages ?? "—"}</td>
                      <td>
                        {j.has_download && j.status === "done" ? (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => Api.downloadPrint(j.id)}
                          >
                            Download
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      )}

      <Dialog
        open={hitlOpen}
        title="Confirm label print"
        onClose={() => {
          setHitlOpen(false);
          setHitlValidation(null);
        }}
      >
        {hitlValidation ? (
          <>
            <p style={{ margin: 0 }}>
              <strong>
                {(hitlValidation.new_printable.length || 0) +
                  (hitlValidation.already_printed.length || 0)}{" "}
                selected
              </strong>
            </p>
            <p style={{ margin: 0 }}>
              {hitlValidation.new_printable.length} unprinted
              <br />
              {hitlValidation.already_printed.length} already printed
            </p>
            <p style={{ margin: 0, color: "var(--muted)" }}>
              Select All never silently reprints. Choose Print Unprinted or explicitly
              Reprint All.
            </p>
          </>
        ) : null}
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
              Print {hitlValidation.new_printable.length} Unprinted
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-accent"
            onClick={() => void confirmReprint(true)}
          >
            Reprint All{" "}
            {(hitlValidation?.new_printable.length || 0) +
              (hitlValidation?.already_printed.length || 0)}
          </button>
        </div>
      </Dialog>
    </div>
  );
}
