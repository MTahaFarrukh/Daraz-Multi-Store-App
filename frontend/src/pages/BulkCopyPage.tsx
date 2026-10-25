import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { useStores } from "@/hooks/queries/useStores";
import { useProductCreateEnabled } from "@/hooks/useProductCreateEnabled";
import { hasCapability } from "@/lib/capabilities";
import {
  ConfirmDialog,
  EmptyState,
  ErrorBanner,
  LoadingState,
  PageHeader,
  StatusBadge,
  SuccessBanner,
} from "@/components/ui/Primitives";

type CatalogItem = {
  product_id?: string;
  daraz_item_id?: string;
  title?: string;
  thumbnail_url?: string | null;
  variants_count?: number;
  detail_complete?: boolean;
  eligible?: boolean;
};

type JobRow = {
  id?: string;
  status?: string;
  total_items?: number;
  completed_items?: number;
  verified_count?: number;
  failed_count?: number;
  needs_attention_count?: number;
  needs_reconciliation_count?: number;
  skipped_count?: number;
  catalog_incomplete?: boolean;
  catalog_warning?: string | null;
};

const STATUS_TONE: Record<string, "ok" | "warn" | "danger" | "muted"> = {
  verified: "ok",
  skipped: "ok",
  failed: "danger",
  needs_attention: "warn",
  needs_reconciliation: "warn",
  preparing: "muted",
  creating: "muted",
  verifying: "muted",
  queued: "muted",
};

const JOB_STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
};

function humanJobStatus(status: string | undefined): string {
  const key = String(status || "").toLowerCase();
  return JOB_STATUS_LABEL[key] || (status || "Unknown");
}

export function BulkCopyPage() {
  const { me } = useAuth();
  const workspaceId = me?.workspace?.id;
  const canCreate = hasCapability(me?.workspace, "product.create");
  const productCreateEnabled = useProductCreateEnabled();
  const storesQuery = useStores(workspaceId);
  const stores = storesQuery.data || [];
  const createGateOn = productCreateEnabled === true;

  const [sourceId, setSourceId] = useState("");
  const [destId, setDestId] = useState("");
  const [page, setPage] = useState(1);
  const [catalog, setCatalog] = useState<{
    total: number;
    eligible_count: number;
    eligible_item_ids: string[];
    catalog_incomplete: boolean;
    catalog_warning?: string | null;
    items: CatalogItem[];
  } | null>(null);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [selectAll, setSelectAll] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [confirmStart, setConfirmStart] = useState(false);
  const [itemPage, setItemPage] = useState(1);

  const sameStore = Boolean(sourceId && destId && sourceId === destId);
  const selectedCount = useMemo(() => {
    if (!catalog) return 0;
    if (selectAll) return Math.max(0, catalog.eligible_count - excluded.size);
    return catalog.eligible_item_ids.filter((id) => !excluded.has(id)).length;
  }, [catalog, selectAll, excluded]);

  const jobQuery = useQuery({
    queryKey: ["bulk-copy-job", workspaceId, jobId],
    queryFn: () => Api.getBulkCopyJob(jobId!),
    enabled: Boolean(workspaceId && jobId),
    refetchInterval: (q) => {
      const st = String((q.state.data?.job as JobRow | undefined)?.status || "");
      return st === "queued" || st === "running" ? 1500 : false;
    },
  });

  const itemsQuery = useQuery({
    queryKey: ["bulk-copy-items", workspaceId, jobId, itemPage],
    queryFn: () =>
      Api.listBulkCopyJobItems(jobId!, { page: itemPage, page_size: 40 }),
    enabled: Boolean(workspaceId && jobId),
    refetchInterval: () => {
      const st = String((jobQuery.data?.job as JobRow | undefined)?.status || "");
      return st === "queued" || st === "running" ? 2000 : false;
    },
  });

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const j = params.get("job");
    if (j) setJobId(j);
  }, []);

  async function loadCatalog(refresh = true, pageOverride?: number) {
    if (!sourceId) {
      setError("Choose a source store first");
      return;
    }
    const pageNum = pageOverride ?? page;
    setError("");
    setOk("");
    setBusy(refresh ? "Loading catalog…" : "Refreshing preview…");
    try {
      const data = await Api.bulkCopyCatalog({
        source_store_id: sourceId,
        refresh,
        page: pageNum,
        page_size: 40,
      });
      if (pageOverride != null) setPage(pageOverride);
      setCatalog({
        total: data.total,
        eligible_count: data.eligible_count,
        eligible_item_ids: data.eligible_item_ids || [],
        catalog_incomplete: data.catalog_incomplete,
        catalog_warning: data.catalog_warning,
        items: data.items || [],
      });
      setExcluded(new Set());
      setSelectAll(true);
      setOk(
        `Discovered ${data.eligible_count} eligible of ${data.total} products` +
          (data.catalog_incomplete ? " (catalog may be incomplete)" : "")
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function startCopy() {
    if (!sourceId || !destId || sameStore) return;
    if (!createGateOn) {
      setConfirmStart(false);
      setError(
        "Product creation is disabled on this server. Enable ALLOW_PRODUCT_CREATE after supervised proof before starting a bulk copy."
      );
      return;
    }
    setConfirmStart(false);
    setBusy("Starting copy job…");
    setError("");
    setOk("");
    try {
      const exclude_item_ids = Array.from(excluded);
      const body = selectAll
        ? {
            source_store_id: sourceId,
            destination_store_id: destId,
            select_all: true,
            exclude_item_ids,
            refresh_catalog: false,
          }
        : {
            source_store_id: sourceId,
            destination_store_id: destId,
            select_all: false,
            product_item_ids: (catalog?.eligible_item_ids || []).filter(
              (id) => !excluded.has(id)
            ),
            exclude_item_ids,
            refresh_catalog: false,
          };
      const res = await Api.startBulkCopyJob(body);
      const job = res.job as JobRow;
      setJobId(String(job.id || ""));
      if (res.duplicate_prevented) {
        setOk("Existing job reused — duplicate submission prevented.");
      } else {
        setOk(`Copy started for ${job.total_items ?? selectedCount} products.`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  function toggleExclude(itemId: string) {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
    setSelectAll(true);
  }

  const job = jobQuery.data?.job as JobRow | undefined;
  const jobItems = (itemsQuery.data?.items || []) as Array<{
    id?: string;
    source_daraz_item_id?: string;
    source_title?: string;
    thumbnail_url?: string | null;
    variants_count?: number;
    status?: string;
    status_label?: string;
    reason?: string | null;
    attempt_id?: string | null;
  }>;

  if (!canCreate) {
    return (
      <div className="stack">
        <PageHeader
          title="Copy Products Between Stores"
          description="Your role cannot create products in this workspace."
        />
        <EmptyState title="Not available for your role" />
      </div>
    );
  }

  return (
    <div className="stack">
      <PageHeader
        title="Copy Products Between Stores"
        description="Select a source store and destination store, load the full source catalog, then copy all eligible products in one job."
        actions={
          <Link to="/app/products" className="btn">
            Back to Products
          </Link>
        }
      />
      <ErrorBanner message={error} />
      <SuccessBanner message={ok} />

      <section className="card">
        <div className="row filters-row" style={{ flexWrap: "wrap", gap: "0.75rem" }}>
          <label>
            Source store
            <select
              value={sourceId}
              onChange={(e) => {
                setSourceId(e.target.value);
                setCatalog(null);
                setJobId(null);
                setPage(1);
                setExcluded(new Set());
              }}
            >
              <option value="">Select source…</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.display_name || s.store_name || s.store_id}
                </option>
              ))}
            </select>
          </label>
          <label>
            Destination store
            <select
              value={destId}
              onChange={(e) => {
                setDestId(e.target.value);
                setJobId(null);
              }}
            >
              <option value="">Select destination…</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.display_name || s.store_name || s.store_id}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn btn-primary"
            style={{ alignSelf: "end" }}
            disabled={!sourceId || Boolean(busy)}
            onClick={() => void loadCatalog(true)}
          >
            Load catalog
          </button>
        </div>
        {sameStore ? (
          <p className="banner banner-error" style={{ marginTop: "0.75rem" }}>
            Source and destination must be different stores.
          </p>
        ) : null}
        {productCreateEnabled === false ? (
          <p className="banner banner-warn" style={{ marginTop: "0.75rem" }}>
            Product creation is disabled (ALLOW_PRODUCT_CREATE). You can load the catalog, but
            starting a copy is blocked until creation is enabled after supervised proof.
          </p>
        ) : null}
      </section>

      {busy && !jobId ? <LoadingState label={busy} /> : null}

      {catalog ? (
        <section className="card">
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: "0.75rem" }}>
            <div>
              <h3 className="section-title" style={{ marginBottom: "0.25rem" }}>
                Catalog preview
              </h3>
              <p className="muted-line" style={{ margin: 0 }}>
                {catalog.eligible_count} eligible · {catalog.total} total discovered
                {catalog.catalog_incomplete ? " · incomplete sync warning" : ""}
              </p>
              {catalog.catalog_warning ? (
                <p className="banner banner-warn" style={{ marginTop: "0.5rem" }}>
                  {catalog.catalog_warning}
                </p>
              ) : null}
            </div>
            <div className="row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setExcluded(new Set());
                  setSelectAll(true);
                }}
              >
                Select All
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setExcluded(new Set(catalog.eligible_item_ids));
                  setSelectAll(true);
                }}
              >
                Clear Selection
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={
                  sameStore ||
                  !destId ||
                  selectedCount === 0 ||
                  Boolean(busy) ||
                  !createGateOn
                }
                onClick={() => setConfirmStart(true)}
              >
                Copy Products ({selectedCount})
              </button>
            </div>
          </div>

          <div className="table-wrap" style={{ marginTop: "0.85rem" }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Include</th>
                  <th>Product</th>
                  <th>Variants</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {catalog.items.map((row) => {
                  const iid = String(row.daraz_item_id || "");
                  const included = selectAll ? !excluded.has(iid) : !excluded.has(iid);
                  return (
                    <tr key={iid || row.product_id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={included && Boolean(row.eligible)}
                          disabled={!row.eligible}
                          onChange={() => toggleExclude(iid)}
                          aria-label={`Include ${row.title || iid}`}
                        />
                      </td>
                      <td>
                        <div className="row" style={{ gap: "0.6rem", alignItems: "center" }}>
                          {row.thumbnail_url ? (
                            <img src={row.thumbnail_url} alt="" className="inv-thumb" width={36} height={36} />
                          ) : (
                            <div className="inv-thumb inv-thumb-empty" />
                          )}
                          <div>
                            <div className="inv-title-truncate" title={row.title}>
                              {row.title || "—"}
                            </div>
                            <div className="muted-line" style={{ fontSize: "0.75rem" }}>
                              {iid}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>{row.variants_count ?? "—"}</td>
                      <td>
                        <StatusBadge tone={row.detail_complete ? "ok" : "warn"}>
                          {row.detail_complete ? "Complete" : "May need detail"}
                        </StatusBadge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ justifyContent: "space-between", gap: "0.5rem", marginTop: "0.75rem", flexWrap: "wrap" }}>
            <p className="muted-line" style={{ margin: 0 }}>
              Select All applies to all {catalog.eligible_count} eligible products across the catalog,
              not only this page. Uncheck rows to exclude them.
            </p>
            <div className="row" style={{ gap: "0.5rem" }}>
              <button
                type="button"
                className="btn"
                disabled={page <= 1 || Boolean(busy)}
                onClick={() => void loadCatalog(false, Math.max(1, page - 1))}
              >
                Previous page
              </button>
              <button
                type="button"
                className="btn"
                disabled={Boolean(busy) || catalog.items.length < 40}
                onClick={() => void loadCatalog(false, page + 1)}
              >
                Next page
              </button>
            </div>
          </div>
        </section>
      ) : null}

      {jobId && job ? (
        <section className="card">
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: "0.75rem" }}>
            <div>
              <h3 className="section-title" style={{ marginBottom: "0.25rem" }}>
                Job progress
              </h3>
              <p className="muted-line" style={{ margin: 0 }}>
                Status: {humanJobStatus(String(job.status))} · {job.completed_items ?? 0}/
                {job.total_items ?? 0} finished
              </p>
            </div>
            <div className="row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
              {(job.status === "partial" || job.status === "failed" || job.status === "completed") &&
              (Number(job.failed_count) > 0 || Number(job.needs_attention_count) > 0) ? (
                <button
                  type="button"
                  className="btn"
                  disabled={Boolean(busy)}
                  onClick={() => {
                    void (async () => {
                      setBusy("Retrying…");
                      try {
                        await Api.retryBulkCopyFailed(jobId);
                        await jobQuery.refetch();
                        await itemsQuery.refetch();
                        setOk("Eligible failed items re-queued.");
                      } catch (e) {
                        setError(e instanceof Error ? e.message : String(e));
                      } finally {
                        setBusy("");
                      }
                    })();
                  }}
                >
                  Retry Failed
                </button>
              ) : null}
              {(job.status === "partial" || job.status === "failed" || job.status === "completed") &&
              Number(job.needs_reconciliation_count) > 0 ? (
                <button
                  type="button"
                  className="btn"
                  disabled={Boolean(busy)}
                  onClick={() => {
                    void (async () => {
                      setBusy("Reconciling…");
                      try {
                        const out = await Api.reconcileBulkCopyJob(jobId);
                        setOk(
                          `Reconciled ${out.reconciled}; unresolved ${out.unresolved}`
                        );
                        await jobQuery.refetch();
                        await itemsQuery.refetch();
                      } catch (e) {
                        setError(e instanceof Error ? e.message : String(e));
                      } finally {
                        setBusy("");
                      }
                    })();
                  }}
                >
                  Reconcile Uncertain
                </button>
              ) : null}
            </div>
          </div>

          <div className="grid-stats" style={{ marginTop: "0.85rem" }}>
            <div className="card stat-card">
              <p className="label">Verified</p>
              <p className="value">{job.verified_count ?? 0}</p>
            </div>
            <div className="card stat-card">
              <p className="label">Already Copied</p>
              <p className="value">{job.skipped_count ?? 0}</p>
            </div>
            <div className="card stat-card">
              <p className="label">Needs Attention</p>
              <p className="value">{job.needs_attention_count ?? 0}</p>
            </div>
            <div className="card stat-card">
              <p className="label">Needs Reconciliation</p>
              <p className="value">{job.needs_reconciliation_count ?? 0}</p>
            </div>
            <div className="card stat-card">
              <p className="label">Failed</p>
              <p className="value">{job.failed_count ?? 0}</p>
            </div>
          </div>

          {itemsQuery.isLoading ? <LoadingState label="Loading results…" /> : null}
          <div className="table-wrap" style={{ marginTop: "0.85rem" }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Variants</th>
                  <th>Status</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {jobItems.map((row) => (
                  <tr key={String(row.id)}>
                    <td>
                      <div className="row" style={{ gap: "0.6rem", alignItems: "center" }}>
                        {row.thumbnail_url ? (
                          <img src={row.thumbnail_url} alt="" className="inv-thumb" width={36} height={36} />
                        ) : (
                          <div className="inv-thumb inv-thumb-empty" />
                        )}
                        <div>
                          <div className="inv-title-truncate" title={row.source_title || undefined}>
                            {row.source_title || "—"}
                          </div>
                          <div className="muted-line" style={{ fontSize: "0.75rem" }}>
                            {row.source_daraz_item_id}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td>{row.variants_count ?? "—"}</td>
                    <td>
                      <StatusBadge tone={STATUS_TONE[String(row.status)] || "muted"}>
                        {row.status_label || row.status}
                      </StatusBadge>
                    </td>
                    <td className="muted-line">{row.reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ justifyContent: "flex-end", gap: "0.5rem", marginTop: "0.75rem" }}>
            <button
              type="button"
              className="btn"
              disabled={itemPage <= 1}
              onClick={() => setItemPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </button>
            <button
              type="button"
              className="btn"
              disabled={(itemsQuery.data?.items || []).length < 40}
              onClick={() => setItemPage((p) => p + 1)}
            >
              Next
            </button>
          </div>
        </section>
      ) : null}

      <ConfirmDialog
        open={confirmStart}
        title="Start bulk copy?"
        message={`Copy ${selectedCount} product(s) from the source store to the destination. Each product uses an independent create attempt with duplicate protection.`}
        confirmLabel="Copy Products"
        onCancel={() => setConfirmStart(false)}
        onConfirm={() => void startCopy()}
      />
    </div>
  );
}
