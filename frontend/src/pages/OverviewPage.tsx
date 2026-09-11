import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Api } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import {
  EmptyState,
  ErrorBanner,
  PageHeader,
  StatCard,
  StatusBadge,
} from "@/components/ui/Primitives";
import { StorePerformancePanel } from "@/components/performance/StorePerformancePanel";
import type { StoreView } from "@/types/api";

function greetingName(email?: string | null): string {
  if (!email) return "there";
  const local = email.split("@")[0] || "there";
  return local.charAt(0).toUpperCase() + local.slice(1);
}

function greetingHour(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Assalamualaikum";
}

export function OverviewPage() {
  const { me } = useAuth();
  const [stores, setStores] = useState<StoreView[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await Api.listStores();
        if (!cancelled) setStores(data.stores || []);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const needsAttention = useMemo(
    () =>
      stores.filter(
        (s) =>
          s.needs_attention === true ||
          s.connection_status === "needs_reconnection" ||
          (s.needs_attention == null &&
            s.access_token_expires_in_seconds != null &&
            s.access_token_expires_in_seconds <= 10 * 86400)
      ),
    [stores]
  );

  return (
    <div>
      <PageHeader
        title={`${greetingHour()}, ${greetingName(me?.user.email)}`}
        description="Here's what's happening across your stores."
      />
      <ErrorBanner message={error} />

      <div className="grid-stats">
        <StatCard
          label="Connected stores"
          value={loading ? "…" : stores.length}
          hint="From your workspace"
        />
        <StatCard
          label="Ready to ship"
          value="—"
          unavailable
          hint="Open Shipping to load RTS orders for selected stores"
        />
        <StatCard
          label="New orders today"
          value="—"
          unavailable
          hint="Requires order metrics API (later phase)"
        />
        <StatCard
          label="Revenue today"
          value="—"
          unavailable
          hint="Requires finance ingestion (later phase)"
        />
      </div>

      <div className="stack">
        <StorePerformancePanel compact />

        <section className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3 className="section-title" style={{ margin: 0 }}>
              Needs attention
            </h3>
            <Link to="/app/stores" style={{ fontWeight: 700, color: "var(--teal-deep)", fontSize: "0.9rem" }}>
              View all stores →
            </Link>
          </div>
          {needsAttention.length === 0 && stores.length > 0 ? (
            <p style={{ margin: "0.75rem 0 0", color: "var(--muted)" }}>
              No urgent store connection issues detected.
            </p>
          ) : null}
          {stores.length === 0 && !loading ? (
            <EmptyState
              title="Connect your first Daraz store"
              description="OAuth connects a seller account into this workspace."
              action={
                <Link className="btn btn-primary" to="/app/stores">
                  Go to Stores
                </Link>
              }
            />
          ) : null}
          {needsAttention.length > 0 ? (
            <ul style={{ margin: "0.75rem 0 0", paddingLeft: "1.1rem" }}>
              {needsAttention.map((s) => (
                <li key={s.store_id} style={{ marginBottom: "0.35rem" }}>
                  <strong>{s.display_name || s.store_id}</strong>{" "}
                  <StatusBadge
                    tone={
                      s.connection_status === "needs_reconnection" ||
                      (s.access_token_expires_in_seconds || 0) <= 3 * 86400
                        ? "danger"
                        : "warn"
                    }
                  >
                    {s.connection_status === "needs_reconnection"
                      ? "needs reconnection"
                      : "token expiring"}
                  </StatusBadge>
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        <section className="card">
          <h3 className="section-title">Quick actions</h3>
          <div className="row">
            <Link className="btn btn-primary" to="/app/shipping">
              Shipping / labels
            </Link>
            <Link className="btn btn-ghost" to="/app/stores">
              Manage stores
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
