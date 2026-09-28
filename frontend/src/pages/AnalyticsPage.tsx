import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { useStores } from "@/hooks/queries/useStores";
import { SimpleBarChart, SimpleLineChart } from "@/components/charts/SimpleCharts";
import {
  EmptyState,
  ErrorBanner,
  LoadingState,
  PageHeader,
  StatCard,
} from "@/components/ui/Primitives";
import {
  ANALYTICS_EMPTY_MESSAGE,
  formatCount,
  formatMomPct,
  formatMoneyAmount,
} from "@/lib/analyticsFormat";

function currentYearMonth(): { year: number; month: number } {
  const d = new Date();
  return { year: d.getFullYear(), month: d.getMonth() + 1 };
}

export function AnalyticsPage() {
  const { me } = useAuth();
  const workspaceId = me?.workspace?.id;
  const storesQuery = useStores(workspaceId);
  const stores = storesQuery.data || [];
  const initial = currentYearMonth();
  const [year, setYear] = useState(initial.year);
  const [month, setMonth] = useState(initial.month);
  const [storeId, setStoreId] = useState("");

  const query = useQuery({
    queryKey: ["analytics", workspaceId, year, month, storeId],
    queryFn: () =>
      Api.analyticsSummary({
        year,
        month,
        store_id: storeId || undefined,
      }),
    enabled: Boolean(workspaceId),
  });

  const data = query.data;
  const summary = data?.summary;
  const empty = Boolean(data && summary && summary.orders === 0);

  const orderPoints = useMemo(
    () =>
      (data?.orders_over_time || []).map((d) => ({
        label: d.date.slice(8),
        value: d.orders,
      })),
    [data]
  );
  const salesPoints = useMemo(
    () =>
      (data?.gross_sales_over_time || []).map((d) => ({
        label: d.date.slice(8),
        value: d.gross_sales,
      })),
    [data]
  );
  const statusPoints = useMemo(
    () =>
      (data?.status_distribution || []).map((d) => ({
        label: d.status,
        value: d.count,
      })),
    [data]
  );
  const storePoints = useMemo(
    () =>
      (data?.store_comparison || []).map((d) => ({
        label: String(d.store_display_name || d.store_id || "Store"),
        value: d.orders,
      })),
    [data]
  );

  return (
    <div className="stack">
      <PageHeader
        title="Analytics"
        description="Order and gross sales trends from local workspace order data. Gross Sales is not profit or payout."
      />
      <ErrorBanner message={query.error instanceof Error ? query.error.message : ""} />

      <section className="card">
        <div className="row filters-row" style={{ flexWrap: "wrap", gap: "0.75rem" }}>
          <label>
            Month
            <input
              type="month"
              value={`${year}-${String(month).padStart(2, "0")}`}
              onChange={(e) => {
                const [y, m] = e.target.value.split("-").map(Number);
                if (y) setYear(y);
                if (m) setMonth(m);
              }}
            />
          </label>
          <label>
            Store
            <select value={storeId} onChange={(e) => setStoreId(e.target.value)}>
              <option value="">All stores</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.display_name || s.store_name || s.store_id}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      {query.isLoading ? <LoadingState label="Loading analytics…" /> : null}

      {!query.isLoading && data ? (
        <>
          <div className="grid-stats">
            <StatCard label="Orders" value={formatCount(summary?.orders)} />
            <StatCard
              label="Gross Sales"
              value={formatMoneyAmount(summary?.gross_sales)}
              hint="Sum of order prices — not net revenue"
            />
            <StatCard
              label="AOV"
              value={
                summary?.average_order_value == null
                  ? "—"
                  : formatMoneyAmount(summary.average_order_value)
              }
            />
            <StatCard label="MoM Orders" value={formatMomPct(summary?.mom_orders_pct)} />
            <StatCard
              label="MoM Gross Sales"
              value={formatMomPct(summary?.mom_gross_sales_pct)}
            />
          </div>

          {empty ? (
            <EmptyState title={ANALYTICS_EMPTY_MESSAGE} description="Sync Orders for this month to populate charts." />
          ) : (
            <div className="analytics-grid">
              <section className="card">
                <h3 className="section-title">Orders trend</h3>
                <SimpleLineChart points={orderPoints} emptyLabel={ANALYTICS_EMPTY_MESSAGE} />
              </section>
              <section className="card">
                <h3 className="section-title">Gross Sales trend</h3>
                <SimpleLineChart
                  points={salesPoints}
                  emptyLabel={ANALYTICS_EMPTY_MESSAGE}
                  valuePrefix="PKR "
                />
              </section>
              <section className="card">
                <h3 className="section-title">Status distribution</h3>
                <SimpleBarChart points={statusPoints} emptyLabel={ANALYTICS_EMPTY_MESSAGE} />
              </section>
              <section className="card">
                <h3 className="section-title">Store comparison</h3>
                <SimpleBarChart points={storePoints} emptyLabel={ANALYTICS_EMPTY_MESSAGE} />
              </section>
            </div>
          )}

          <section className="card">
            <h3 className="section-title">Store performance</h3>
            {!data.store_comparison?.length ? (
              <p className="muted-line" style={{ margin: 0 }}>
                No store breakdown for this month.
              </p>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Store</th>
                      <th>Orders</th>
                      <th>Gross Sales</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.store_comparison.map((row) => (
                      <tr key={String(row.store_id || row.store_display_name)}>
                        <td>{row.store_display_name || row.store_id}</td>
                        <td>{row.orders}</td>
                        <td>{formatMoneyAmount(row.gross_sales)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
