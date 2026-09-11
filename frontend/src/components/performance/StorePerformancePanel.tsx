import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Api } from "@/lib/api";
import { EmptyState, ErrorBanner, StatusBadge } from "@/components/ui/Primitives";
import type { PerformanceLeaderboardResponse, PerformanceRow } from "@/types/api";

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

function monthLabel(year: number, month: number): string {
  return `${MONTH_NAMES[month - 1] || month} ${year}`;
}

function formatGrowth(pct: number | null | undefined): string {
  if (pct == null || Number.isNaN(pct)) return "—";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(pct % 1 === 0 ? 0 : 1)}%`;
}

function growthTone(pct: number | null | undefined): "ok" | "danger" | "neutral" {
  if (pct == null) return "neutral";
  if (pct > 0) return "ok";
  if (pct < 0) return "danger";
  return "neutral";
}

function formatMoney(value: number | null | undefined, currency = "PKR"): string {
  if (value == null) return "—";
  return `${currency} ${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "Unknown";
  const sec = Math.round((Date.now() - t) / 1000);
  if (sec < 45) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function syncLabel(status: string | null | undefined): string | null {
  if (!status || status === "ok") return null;
  if (status === "partial") return "Needs refresh";
  if (status === "error") return "Connection issue";
  return "Unavailable";
}

type Props = {
  compact?: boolean;
};

export function StorePerformancePanel({ compact = false }: Props) {
  const [data, setData] = useState<PerformanceLeaderboardResponse | null>(null);
  const [months, setMonths] = useState<Array<{ year: number; month: number }>>([]);
  const [year, setYear] = useState<number | null>(null);
  const [month, setMonth] = useState<number | null>(null);
  const [metric, setMetric] = useState<"orders" | "gross_sales">("orders");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [ready, setReady] = useState(false);

  const refreshMonths = useCallback(async () => {
    const res = await Api.performanceMonths();
    setMonths(res.months || []);
    return res;
  }, []);

  const loadBoard = useCallback(async (y: number, m: number, met: "orders" | "gross_sales") => {
    const board = await Api.storePerformance(y, m, met);
    setData(board);
    if (board.gross_sales_enabled === false && met === "gross_sales") {
      setMetric("orders");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const res = await refreshMonths();
        if (cancelled) return;
        const y = res.current.year;
        const m = res.current.month;
        setYear(y);
        setMonth(m);
        await loadBoard(y, m, "orders");
        if (!cancelled) setReady(true);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load performance");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshMonths, loadBoard]);

  useEffect(() => {
    if (!ready || year == null || month == null) return;
    let cancelled = false;
    (async () => {
      setError("");
      try {
        await loadBoard(year, month, metric);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load performance");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [year, month, metric, ready, loadBoard]);

  const onRefresh = async () => {
    if (year == null || month == null || syncing) return;
    setSyncing(true);
    setError("");
    try {
      const summary = await Api.syncStorePerformance(year, month, true);
      await refreshMonths();
      await loadBoard(year, month, metric);
      if (summary.stores_error > 0 && summary.stores_ok === 0 && summary.stores_partial === 0) {
        setError(`Sync finished with errors for all ${summary.stores_total} stores.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const rows = data?.leaderboard || [];
  const grossEnabled = data?.gross_sales_enabled !== false;
  const top3 = useMemo(() => rows.slice(0, 3), [rows]);
  const growthKey = metric === "gross_sales" ? "gross_sales_growth_pct" : "orders_growth_pct";

  const periodOptions = useMemo(() => {
    const map = new Map<string, { year: number; month: number }>();
    for (const item of months) {
      map.set(`${item.year}-${item.month}`, item);
    }
    if (year != null && month != null) {
      map.set(`${year}-${month}`, { year, month });
    }
    return Array.from(map.values()).sort((a, b) => b.year - a.year || b.month - a.month);
  }, [months, year, month]);

  return (
    <section className="card perf-panel">
      <div className="perf-header">
        <div>
          <h3 className="section-title" style={{ margin: 0 }}>
            Store Performance
          </h3>
          <p className="muted-line">
            Scope: My Workspace · Last updated: {relativeTime(data?.last_synced_at)}
          </p>
        </div>
        <div className="row perf-controls">
          <label className="field-inline">
            <span className="sr-only">Month</span>
            <select
              value={year != null && month != null ? `${year}-${month}` : ""}
              onChange={(e) => {
                const [y, m] = e.target.value.split("-").map(Number);
                setYear(y);
                setMonth(m);
              }}
              disabled={loading || syncing}
            >
              {periodOptions.map((p) => (
                <option key={`${p.year}-${p.month}`} value={`${p.year}-${p.month}`}>
                  {monthLabel(p.year, p.month)}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onRefresh}
            disabled={syncing || loading || year == null}
          >
            {syncing ? "Refreshing…" : "Refresh performance"}
          </button>
        </div>
      </div>

      <ErrorBanner message={error} />

      <div className="tabs" role="tablist" aria-label="Rank by">
        <button
          type="button"
          className={`tab ${metric === "orders" ? "active" : ""}`}
          onClick={() => setMetric("orders")}
          disabled={syncing}
        >
          Orders
        </button>
        <button
          type="button"
          className={`tab ${metric === "gross_sales" ? "active" : ""}`}
          onClick={() => setMetric("gross_sales")}
          disabled={!grossEnabled || syncing}
          title={grossEnabled ? undefined : "Gross Sales unavailable until pagination is reliable"}
        >
          Gross Sales
        </button>
      </div>

      {loading && !data ? <p className="muted-line">Loading performance…</p> : null}

      {!loading && rows.length === 0 ? (
        <EmptyState
          title="No performance data yet"
          description="Sync your connected stores to build this month’s workspace leaderboard."
          action={
            <button
              type="button"
              className="btn btn-primary"
              onClick={onRefresh}
              disabled={syncing}
            >
              {syncing ? "Syncing…" : "Sync performance"}
            </button>
          }
        />
      ) : null}

      {rows.length > 0 ? (
        <>
          <div className="perf-podium" aria-label="Top performers">
            {top3.map((row) => (
              <TopCard key={String(row.store_id)} row={row} growthKey={growthKey} metric={metric} />
            ))}
          </div>

          <div className="table-wrap stores-table-desktop">
            <table className="data">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Store</th>
                  <th>Orders</th>
                  {grossEnabled ? <th>Gross Sales</th> : null}
                  <th>Change</th>
                  <th>Last Updated</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={String(row.store_id)}>
                    <td>#{row.rank}</td>
                    <td>
                      <strong>{row.display_name || "Store"}</strong>
                      {syncLabel(row.sync_status) ? (
                        <>
                          {" "}
                          <StatusBadge tone={row.sync_status === "error" ? "danger" : "warn"}>
                            {syncLabel(row.sync_status)}
                          </StatusBadge>
                        </>
                      ) : null}
                    </td>
                    <td>{row.orders_count.toLocaleString()}</td>
                    {grossEnabled ? <td>{formatMoney(row.gross_sales, row.currency)}</td> : null}
                    <td className={`growth growth-${growthTone(row[growthKey])}`}>
                      {formatGrowth(row[growthKey])}
                    </td>
                    <td>{relativeTime(row.orders_synced_at || row.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="perf-mobile-list stores-table-mobile">
            {rows.map((row) => (
              <article key={String(row.store_id)} className="perf-mobile-card">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <strong>
                    #{row.rank} {row.display_name || "Store"}
                  </strong>
                  <span className={`growth growth-${growthTone(row[growthKey])}`}>
                    {formatGrowth(row[growthKey])}
                  </span>
                </div>
                <p className="muted-line" style={{ margin: 0 }}>
                  {row.orders_count.toLocaleString()} orders
                  {grossEnabled ? ` · ${formatMoney(row.gross_sales, row.currency)}` : ""}
                  {" · "}
                  {relativeTime(row.orders_synced_at || row.updated_at)}
                </p>
              </article>
            ))}
          </div>
        </>
      ) : null}

      {compact ? (
        <p className="muted-line" style={{ marginTop: "0.85rem" }}>
          <Link to="/app/performance" style={{ fontWeight: 700, color: "var(--teal-deep)" }}>
            View full performance →
          </Link>
        </p>
      ) : null}
    </section>
  );
}

function TopCard({
  row,
  growthKey,
  metric,
}: {
  row: PerformanceRow;
  growthKey: "orders_growth_pct" | "gross_sales_growth_pct";
  metric: "orders" | "gross_sales";
}) {
  const growth = row[growthKey];
  return (
    <article className={`perf-top-card rank-${row.rank}`}>
      <div className="perf-rank">#{row.rank}</div>
      <h4>{row.display_name || "Store"}</h4>
      <p className="perf-metric">
        {metric === "gross_sales"
          ? formatMoney(row.gross_sales, row.currency)
          : `${row.orders_count.toLocaleString()} orders`}
      </p>
      <p className={`growth growth-${growthTone(growth)}`}>{formatGrowth(growth)}</p>
    </article>
  );
}
