import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { canReadFinance, canSyncFinance } from "@/lib/capabilities";
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
  FINANCE_EMPTY_MESSAGE,
  financeNotSynced,
  financePartialWarning,
  formatFinanceAmount,
} from "@/lib/financeUi";
import type { FinanceSyncResponse } from "@/types/api";

type Tab = "overview" | "transactions" | "payouts";

export function FinancePage() {
  const { me } = useAuth();
  const workspace = me?.workspace;
  const workspaceId = workspace?.id;
  const canRead = canReadFinance(workspace);
  const canSync = canSyncFinance(workspace);
  const qc = useQueryClient();
  const storesQuery = useStores(workspaceId);
  const stores = storesQuery.data || [];

  const [tab, setTab] = useState<Tab>("overview");
  const [selected, setSelected] = useState<string[]>([]);
  const [storeFilter, setStoreFilter] = useState("");
  const [page, setPage] = useState(1);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [lastSync, setLastSync] = useState<FinanceSyncResponse | null>(null);

  const summaryQuery = useQuery({
    queryKey: ["finance-summary", workspaceId, storeFilter],
    queryFn: () => Api.financeSummary({ store_id: storeFilter || undefined }),
    enabled: Boolean(workspaceId) && canRead,
  });

  const txQuery = useQuery({
    queryKey: ["finance-tx", workspaceId, storeFilter, page],
    queryFn: () =>
      Api.financeTransactions({
        store_id: storeFilter || undefined,
        page,
        page_size: 50,
      }),
    enabled: Boolean(workspaceId) && canRead && tab === "transactions",
  });

  const payoutQuery = useQuery({
    queryKey: ["finance-payouts", workspaceId, storeFilter, page],
    queryFn: () =>
      Api.financePayouts({
        store_id: storeFilter || undefined,
        page,
        page_size: 50,
      }),
    enabled: Boolean(workspaceId) && canRead && tab === "payouts",
  });

  const syncMutation = useMutation({
    mutationFn: () =>
      Api.syncFinance({
        store_ids: selected,
        lookback_days: 30,
      }),
    onSuccess: (res) => {
      setLastSync(res);
      setOk(
        `Synced ${res.transactions_synced ?? 0} transactions, ${res.payouts_synced ?? 0} payouts.`
      );
      setErr("");
      void qc.invalidateQueries({ queryKey: ["finance-summary", workspaceId] });
      void qc.invalidateQueries({ queryKey: ["finance-tx", workspaceId] });
      void qc.invalidateQueries({ queryKey: ["finance-payouts", workspaceId] });
    },
    onError: (e: unknown) => {
      setErr(e instanceof Error ? e.message : String(e));
      setOk("");
    },
  });

  const summary = summaryQuery.data;
  const notSynced = financeNotSynced(summary);
  const partialWarn = financePartialWarning(lastSync);

  const storeOptions = useMemo(
    () =>
      stores.map((s) => ({
        id: s.store_id,
        label: s.display_name || s.store_name || s.store_id,
      })),
    [stores]
  );

  function toggleStore(id: string) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  }

  if (!canRead) {
    return (
      <div className="stack">
        <PageHeader
          title="Finance"
          description="Financial data synced from connected Daraz stores"
        />
        <EmptyState
          title="Finance is not available for your role"
          description="Ask a workspace owner or admin if you need access to finance data."
        />
      </div>
    );
  }

  return (
    <div className="stack">
      <PageHeader
        title="Finance"
        description="Financial data synced from connected Daraz stores. Read and sync only — no payouts are triggered."
        actions={
          canSync ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={syncMutation.isPending || selected.length === 0}
              title={selected.length === 0 ? "Select at least one store" : undefined}
              onClick={() => syncMutation.mutate()}
            >
              {syncMutation.isPending ? "Syncing…" : "Sync Finance"}
            </button>
          ) : null
        }
      />
      <ErrorBanner message={err || (summaryQuery.error instanceof Error ? summaryQuery.error.message : "")} />
      <SuccessBanner message={ok} />
      {partialWarn ? <div className="banner banner-warn">{partialWarn}</div> : null}

      {canSync ? (
        <section className="card">
          <h3 className="section-title">Stores to sync</h3>
          <p className="muted-line" style={{ marginTop: 0 }}>
            Empty selection is not all stores — pick stores explicitly.
          </p>
          <div className="row" style={{ flexWrap: "wrap", gap: "0.65rem" }}>
            {storeOptions.map((s) => (
              <label key={s.id} className="row" style={{ gap: "0.35rem", alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={selected.includes(s.id)}
                  onChange={() => toggleStore(s.id)}
                />
                {s.label}
              </label>
            ))}
            {!storeOptions.length ? (
              <span className="muted-line">No connected stores.</span>
            ) : null}
          </div>
        </section>
      ) : (
        <p className="muted-line">Your role can view finance data but cannot sync.</p>
      )}

      <div className="grid-stats">
        <StatCard
          label="Gross Sales"
          value={formatFinanceAmount(summary?.gross_sales)}
          unavailable={summary?.gross_sales == null}
          hint="From local orders — not ledger rows"
          title="Canonical Gross Sales from order prices. Finance ledger amounts are never added."
        />
        <StatCard
          label="Known Fees"
          value={formatFinanceAmount(summary?.known_fees)}
          unavailable={summary?.known_fees == null}
          hint="Mapped fee rows only — not a full P&L"
          title="Only fees present on synced finance rows. Unknown transaction types are excluded. Not profit."
        />
        <StatCard
          label="Known Payouts"
          value={formatFinanceAmount(summary?.known_payouts)}
          unavailable={summary?.known_payouts == null}
          hint="Synced payout amounts may be incomplete"
          title="Sum of known payout amounts from Daraz payout status. Incomplete until all stores sync successfully."
        />
        <StatCard
          label="Last Synced"
          value={
            summary?.last_synced
              ? new Date(summary.last_synced).toLocaleString()
              : "—"
          }
          unavailable={!summary?.last_synced}
          hint={
            lastSync?.partial || summary?.completeness?.level === "partial"
              ? "Partial"
              : notSynced
                ? "Not synced"
                : summary?.completeness?.level || undefined
          }
        />
      </div>

      <div className="row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
        {(["overview", "transactions", "payouts"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            className={`btn${tab === t ? " btn-primary" : ""}`}
            onClick={() => {
              setTab(t);
              setPage(1);
            }}
          >
            {t === "overview" ? "Overview" : t === "transactions" ? "Transactions" : "Payouts"}
          </button>
        ))}
        <label style={{ marginLeft: "auto" }}>
          Store filter
          <select
            value={storeFilter}
            onChange={(e) => {
              setStoreFilter(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All</option>
            {storeOptions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {summaryQuery.isLoading ? <LoadingState label="Loading finance…" /> : null}

      {tab === "overview" && !summaryQuery.isLoading ? (
        notSynced ? (
          <EmptyState
            title={FINANCE_EMPTY_MESSAGE}
            description="Select stores and run Sync Finance to pull recent transactions and payouts."
          />
        ) : (
          <section className="card">
            <h3 className="section-title">Overview</h3>
            <p className="muted-line" style={{ margin: 0 }}>
              {summary?.transaction_count ?? 0} transactions · {summary?.payout_count ?? 0}{" "}
              payouts in local cache. Amounts shown only when present in synced rows.
            </p>
          </section>
        )
      ) : null}

      {tab === "transactions" ? (
        txQuery.isLoading ? (
          <LoadingState label="Loading transactions…" />
        ) : !(txQuery.data?.items || []).length ? (
          <EmptyState title={FINANCE_EMPTY_MESSAGE} />
        ) : (
          <section className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Store</th>
                    <th>Reference</th>
                    <th>Type</th>
                    <th>Amount</th>
                    <th>Fee</th>
                    <th>Currency</th>
                  </tr>
                </thead>
                <tbody>
                  {(txQuery.data?.items || []).map((row) => (
                    <tr key={row.id || row.source_transaction_id}>
                      <td>{row.transaction_at || "—"}</td>
                      <td>{row.store_slug || row.store_id || "—"}</td>
                      <td>{row.order_no || row.source_transaction_id || "—"}</td>
                      <td>{row.transaction_type || row.fee_type || "—"}</td>
                      <td>{formatFinanceAmount(row.amount, row.currency || "PKR")}</td>
                      <td>{formatFinanceAmount(row.fee_amount, row.currency || "PKR")}</td>
                      <td>{row.currency || "PKR"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row" style={{ justifyContent: "flex-end", gap: "0.5rem", marginTop: "0.75rem" }}>
              <button type="button" className="btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Previous
              </button>
              <button
                type="button"
                className="btn"
                disabled={(txQuery.data?.items || []).length < 50}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          </section>
        )
      ) : null}

      {tab === "payouts" ? (
        payoutQuery.isLoading ? (
          <LoadingState label="Loading payouts…" />
        ) : !(payoutQuery.data?.items || []).length ? (
          <EmptyState title={FINANCE_EMPTY_MESSAGE} />
        ) : (
          <section className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Store</th>
                    <th>Payout ID</th>
                    <th>Status</th>
                    <th>Amount</th>
                    <th>Date</th>
                  </tr>
                </thead>
                <tbody>
                  {(payoutQuery.data?.items || []).map((row) => (
                    <tr key={row.id || row.source_payout_id}>
                      <td>{row.store_slug || row.store_id || "—"}</td>
                      <td>{row.source_payout_id || row.statement_number || "—"}</td>
                      <td>
                        <StatusBadge tone={row.status === "paid" ? "ok" : "muted"}>
                          {row.status || "unknown"}
                        </StatusBadge>
                      </td>
                      <td>{formatFinanceAmount(row.payout_amount, row.currency || "PKR")}</td>
                      <td>{row.created_at_source || row.synced_at || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )
      ) : null}
    </div>
  );
}
