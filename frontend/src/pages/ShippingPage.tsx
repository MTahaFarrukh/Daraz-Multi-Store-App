import { useCallback, useEffect, useState } from "react";
import { Api } from "@/lib/api";
import { StoreSelector } from "@/components/stores/StoreSelector";
import {
  EmptyState,
  ErrorBanner,
  PageHeader,
  StatusBadge,
  SuccessBanner,
} from "@/components/ui/Primitives";
import type { OrderRow, PrintJobListItem, PrintJobStatus, StoreGroup, StoreView } from "@/types/api";

const SELECTION_KEY = "multistore_shipping_selection_v1";

export function ShippingPage() {
  const [tab, setTab] = useState<"rts" | "history">("rts");
  const [stores, setStores] = useState<StoreView[]>([]);
  const [groups, setGroups] = useState<StoreGroup[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [groupId, setGroupId] = useState("");
  const [limit, setLimit] = useState(10);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState("");
  const [job, setJob] = useState<PrintJobStatus | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [history, setHistory] = useState<PrintJobListItem[]>([]);
  const [historyNote, setHistoryNote] = useState("");

  const loadMeta = useCallback(async () => {
    const [s, g] = await Promise.all([Api.listStores(), Api.listGroups()]);
    setStores(s.stores || []);
    setGroups(g.groups || []);
    const saved = localStorage.getItem(SELECTION_KEY);
    const valid = new Set((s.stores || []).map((x) => x.store_id));
    if (saved) {
      try {
        const ids = (JSON.parse(saved) as string[]).filter((id) => valid.has(id));
        setSelected(ids);
        return;
      } catch {
        /* fall through */
      }
    }
    setSelected((s.stores || []).map((x) => x.store_id));
  }, []);

  useEffect(() => {
    loadMeta().catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  }, [loadMeta]);

  useEffect(() => {
    localStorage.setItem(SELECTION_KEY, JSON.stringify(selected));
  }, [selected]);

  async function loadOrders() {
    setError("");
    setOk("");
    if (!selected.length) {
      setError("Select at least one store");
      return;
    }
    setBusy(`Loading orders (${selected.length} store${selected.length === 1 ? "" : "s"})…`);
    try {
      const data = await Api.listOrders(selected, limit);
      setOrders(data.orders || []);
      setOk(`Loaded ${data.count || 0} orders`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load orders");
    } finally {
      setBusy("");
    }
  }

  async function printLabels() {
    setError("");
    setOk("");
    setJob(null);
    if (!selected.length) {
      setError("Select at least one store");
      return;
    }
    setBusy(`Starting print (${selected.length} store(s), limit ${limit})…`);
    const started = Date.now();
    try {
      const startedJob = await Api.startPrint(selected, limit);
      const jobId = startedJob.job_id;
      if (!jobId) throw new Error("Print job did not return a job_id");
      setActiveJobId(jobId);

      const maxWait = 25 * 60 * 1000;
      while (Date.now() - started < maxWait) {
        const status = await Api.printStatus(jobId);
        setJob({ ...status, id: jobId });
        if (status.message) setBusy(status.message);
        if (status.status === "done") {
          const secs = Math.round((Date.now() - started) / 1000);
          setOk(`PDF ready · ${status.pages ?? "?"} page(s) · ${secs}s`);
          setBusy("");
          try {
            await Api.downloadPrint(jobId);
          } catch {
            /* download may be retried manually */
          }
          return;
        }
        if (status.status === "error") {
          throw new Error(status.error || status.message || "Print failed");
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
      throw new Error("Print timed out — try a lower limit");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Print failed");
      setBusy("");
    }
  }

  async function loadHistory() {
    setError("");
    setHistoryNote("");
    try {
      const data = await Api.listPrintJobs();
      setHistory(data.jobs || []);
      if (!(data.jobs || []).length) {
        setHistoryNote("No print jobs recorded for this workspace yet.");
      }
    } catch (err) {
      // Endpoint may be unavailable on older deploys
      setHistory([]);
      setHistoryNote(
        err instanceof Error
          ? err.message
          : "Print history list is not available. Job-specific download still works after a successful print."
      );
    }
  }

  useEffect(() => {
    if (tab === "history") {
      loadHistory().catch(() => undefined);
    }
  }, [tab]);

  return (
    <div className="stack">
      <PageHeader
        title="Shipping"
        description="Manage ready-to-ship orders and shipping labels across selected stores."
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
              selected={selected}
              onChange={(ids) => {
                setSelected(ids);
                setGroupId("");
              }}
              groups={groups}
              activeGroupId={groupId}
              onGroupChange={(id) => {
                setGroupId(id);
                if (!id) return;
                if (id === "__all__") {
                  setSelected(stores.map((x) => x.store_id));
                  return;
                }
                const g = groups.find((x) => x.id === id);
                if (!g) {
                  setSelected([]);
                  return;
                }
                const valid = new Set(stores.map((x) => x.store_id));
                setSelected(g.store_ids.filter((sid) => valid.has(sid)));
              }}
            />
          </section>

          <section className="card">
            <h3 className="section-title">Ready to ship</h3>
            <div className="toolbar">
              <label className="field" style={{ margin: 0, minWidth: 160 }}>
                <span>Orders per store</span>
                <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
                  {[3, 5, 10, 20, 30].map((n) => (
                    <option key={n} value={n}>
                      {n}
                      {n === 3 ? " (fast)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!selected.length || Boolean(busy)}
                onClick={loadOrders}
              >
                Load orders
              </button>
              <button
                type="button"
                className="btn btn-accent"
                disabled={!selected.length || Boolean(busy)}
                onClick={printLabels}
              >
                Print labels PDF
              </button>
              {job?.status === "done" && activeJobId ? (
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => Api.downloadPrint(activeJobId)}
                >
                  Download PDF
                </button>
              ) : null}
            </div>

            {orders.length === 0 ? (
              <EmptyState
                title="No orders loaded"
                description="Select stores and load ready-to-ship orders. Empty selection never means all stores."
              />
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Store</th>
                      <th>Order ID</th>
                      <th>Items</th>
                      <th>Status</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((o) => {
                      const statuses = Array.isArray(o.statuses)
                        ? o.statuses.join(", ")
                        : o.statuses || "—";
                      return (
                        <tr key={`${o.store_id}-${o.order_id}`}>
                          <td>{o.store_name || o.display_name || o.store_id || "—"}</td>
                          <td>{String(o.order_id ?? "—")}</td>
                          <td>{String(o.items_count ?? "—")}</td>
                          <td>
                            <StatusBadge tone="ok">{statuses}</StatusBadge>
                          </td>
                          <td>{o.created_at || "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {job?.label_details?.length ? (
            <section className="card">
              <h3 className="section-title">Last print</h3>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Store</th>
                      <th>Order ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.label_details.map((row, idx) => (
                      <tr key={`${row.order_id}-${idx}`}>
                        <td>{row.store_name || "—"}</td>
                        <td>{String(row.order_id ?? "—")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
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
    </div>
  );
}
