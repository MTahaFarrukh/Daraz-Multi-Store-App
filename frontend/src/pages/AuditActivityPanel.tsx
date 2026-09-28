import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { canViewAudit, formatAuditAction } from "@/lib/capabilities";
import { useAuth } from "@/hooks/useAuth";
import { ErrorBanner, StatusBadge } from "@/components/ui/Primitives";

export function AuditActivityPanel() {
  const { me } = useAuth();
  const allowed = canViewAudit(me?.workspace);
  const [action, setAction] = useState("");
  const [since, setSince] = useState("");

  const query = useQuery({
    queryKey: ["audit-events", me?.workspace?.id, action, since],
    queryFn: () =>
      Api.listAuditEvents({
        action: action || undefined,
        since: since || undefined,
        limit: 50,
      }),
    enabled: Boolean(me?.workspace?.id) && allowed,
  });

  if (!allowed) {
    return (
      <section className="card">
        <h3 className="section-title">Audit Activity</h3>
        <p className="muted-line" style={{ margin: 0 }}>
          Owner/admin only. Ask a workspace owner if you need activity history.
        </p>
      </section>
    );
  }

  const items = query.data?.items || [];

  return (
    <section className="card">
      <h3 className="section-title">Audit Activity</h3>
      <p className="muted-line" style={{ marginTop: 0 }}>
        Workspace-scoped mutation history. Secrets and tokens are never stored here.
      </p>
      <div className="row" style={{ gap: "0.75rem", flexWrap: "wrap", marginBottom: "0.75rem" }}>
        <label>
          Action
          <input
            value={action}
            onChange={(e) => setAction(e.target.value)}
            placeholder="e.g. store.rename"
          />
        </label>
        <label>
          Since (ISO date)
          <input
            value={since}
            onChange={(e) => setSince(e.target.value)}
            placeholder="2026-01-01"
          />
        </label>
      </div>
      {query.isError ? (
        <ErrorBanner
          message={
            query.error instanceof Error ? query.error.message : "Failed to load audit"
          }
        />
      ) : null}
      {!items.length ? (
        <p className="muted-line" style={{ margin: 0 }}>
          No events yet.
        </p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table" style={{ width: "100%", fontSize: "0.88rem" }}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Entity</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((ev) => (
                <tr key={ev.id}>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {ev.created_at ? new Date(ev.created_at).toLocaleString() : "—"}
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                    {(ev.actor_user_id || "—").slice(0, 8)}
                  </td>
                  <td>{formatAuditAction(ev.action)}</td>
                  <td>
                    {String(ev.entity_type || "")
                      .replace(/_/g, " ")
                      .replace(/^\w/, (c) => c.toUpperCase())}
                    {ev.entity_id ? ` · ${String(ev.entity_id).slice(0, 10)}` : ""}
                  </td>
                  <td>
                    <StatusBadge tone="muted">
                      {String((ev.metadata as { status?: string } | undefined)?.status || "—")}
                    </StatusBadge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
