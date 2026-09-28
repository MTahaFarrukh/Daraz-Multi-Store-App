import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Api } from "@/lib/api";
import { canManageConnections } from "@/lib/capabilities";
import { useAuth } from "@/hooks/useAuth";
import {
  ErrorBanner,
  PageHeader,
  SuccessBanner,
} from "@/components/ui/Primitives";
import type { TrustedConnection } from "@/types/api";

export function ConnectionsPage() {
  const { me } = useAuth();
  const workspace = me?.workspace;
  const canMutate = canManageConnections(workspace);
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState("");

  const query = useQuery({
    queryKey: ["connections", workspace?.id],
    queryFn: () => Api.listConnections(),
    enabled: Boolean(workspace?.id),
  });

  const data = query.data;

  async function refresh() {
    await qc.invalidateQueries({ queryKey: ["connections", workspace?.id] });
  }

  async function run(label: string, fn: () => Promise<unknown>) {
    setError("");
    setOk("");
    setBusy(label);
    try {
      await fn();
      setOk(label);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  const myCode = data?.workspace?.connection_code || workspace?.connection_code || "—";

  const sections = useMemo(
    () => [
      { title: "Incoming Requests", rows: data?.incoming || [], kind: "incoming" as const },
      { title: "Outgoing Requests", rows: data?.outgoing || [], kind: "outgoing" as const },
      { title: "Connected Workspaces", rows: data?.connected || [], kind: "connected" as const },
    ],
    [data]
  );

  return (
    <div className="stack">
      <PageHeader
        title="Connections"
        description="Trusted workspace links for future product sharing. Orders, finance, and tokens stay private."
      />
      <ErrorBanner message={error} />
      <SuccessBanner message={ok} />

      <section className="card">
        <h3 className="section-title">Your connection code</h3>
        <p className="muted-line" style={{ marginTop: 0 }}>
          Share this code only with workspaces you trust. It is not a secret password.
        </p>
        <p style={{ fontFamily: "monospace", fontSize: "1.05rem", margin: 0 }}>{myCode}</p>
      </section>

      {canMutate ? (
        <section className="card">
          <h3 className="section-title">Request connection</h3>
          <div className="row" style={{ gap: "0.75rem", alignItems: "end", flexWrap: "wrap" }}>
            <label style={{ flex: "1 1 220px" }}>
              Target connection code
              <input
                value={code}
                onChange={(e) => setCode(e.target.value.toUpperCase())}
                placeholder="WS-…"
              />
            </label>
            <button
              type="button"
              className="btn btn-primary"
              disabled={Boolean(busy) || !code.trim()}
              onClick={() =>
                void run("Request sent", () => Api.requestConnection(code.trim()))
              }
            >
              Send request
            </button>
          </div>
        </section>
      ) : (
        <p className="muted-line">Your role can view connections but cannot change them.</p>
      )}

      {sections.map((sec) => (
        <section className="card" key={sec.title}>
          <h3 className="section-title">{sec.title}</h3>
          {!sec.rows.length ? (
            <p className="muted-line" style={{ margin: 0 }}>
              None
            </p>
          ) : (
            <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
              {sec.rows.map((row) => (
                <ConnectionRow
                  key={row.id}
                  row={row}
                  kind={sec.kind}
                  canMutate={canMutate}
                  busy={busy}
                  onAccept={() =>
                    void run("Accepted", () => Api.acceptConnection(row.id))
                  }
                  onReject={() =>
                    void run("Rejected", () => Api.rejectConnection(row.id))
                  }
                  onRevoke={() =>
                    void run("Revoked", () => Api.revokeConnection(row.id))
                  }
                  onPerms={(patch) =>
                    void run("Permissions updated", () =>
                      Api.updateConnectionPermissions(row.id, patch)
                    )
                  }
                />
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}

function ConnectionRow({
  row,
  kind,
  canMutate,
  busy,
  onAccept,
  onReject,
  onRevoke,
  onPerms,
}: {
  row: TrustedConnection;
  kind: "incoming" | "outgoing" | "connected";
  canMutate: boolean;
  busy: string;
  onAccept: () => void;
  onReject: () => void;
  onRevoke: () => void;
  onPerms: (p: { view_products?: boolean; copy_products?: boolean }) => void;
}) {
  const peer =
    row.peer_workspace?.name ||
    row.peer_workspace?.connection_code ||
    row.peer_workspace?.id ||
    "Workspace";
  return (
    <li style={{ marginBottom: "0.65rem" }}>
      <strong>{peer}</strong> — {row.status}
      {kind === "connected" ? (
        <span className="muted-line">
          {" "}
          · view {row.view_products ? "on" : "off"} · copy{" "}
          {row.copy_products ? "on" : "off"}
        </span>
      ) : null}
      {canMutate && kind === "incoming" && row.status === "PENDING" ? (
        <span style={{ marginLeft: "0.5rem" }}>
          <button type="button" className="btn btn-primary" disabled={Boolean(busy)} onClick={onAccept}>
            Accept
          </button>{" "}
          <button type="button" className="btn btn-ghost" disabled={Boolean(busy)} onClick={onReject}>
            Reject
          </button>
        </span>
      ) : null}
      {canMutate && kind === "outgoing" && row.status === "PENDING" ? (
        <button
          type="button"
          className="btn btn-ghost"
          style={{ marginLeft: "0.5rem" }}
          disabled={Boolean(busy)}
          onClick={onRevoke}
        >
          Cancel
        </button>
      ) : null}
      {canMutate && kind === "connected" ? (
        <span style={{ marginLeft: "0.5rem", display: "inline-flex", gap: "0.35rem", flexWrap: "wrap" }}>
          <label style={{ fontSize: "0.85rem" }}>
            <input
              type="checkbox"
              checked={Boolean(row.view_products)}
              disabled={Boolean(busy)}
              onChange={(e) => onPerms({ view_products: e.target.checked })}
            />{" "}
            View Products
          </label>
          <label style={{ fontSize: "0.85rem" }}>
            <input
              type="checkbox"
              checked={Boolean(row.copy_products)}
              disabled={Boolean(busy)}
              onChange={(e) => onPerms({ copy_products: e.target.checked })}
            />{" "}
            Copy Products
          </label>
          <button type="button" className="btn btn-ghost" disabled={Boolean(busy)} onClick={onRevoke}>
            Revoke
          </button>
        </span>
      ) : null}
    </li>
  );
}
