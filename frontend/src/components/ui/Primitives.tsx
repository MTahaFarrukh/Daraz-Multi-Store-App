import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-header row" style={{ justifyContent: "space-between" }}>
      <div>
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="row">{actions}</div> : null}
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  unavailable,
}: {
  label: string;
  value: string | number;
  hint?: string;
  unavailable?: boolean;
}) {
  return (
    <div className={`card stat-card${unavailable ? " unavailable" : ""}`}>
      <p className="label">{label}</p>
      <p className="value">{unavailable ? "Not available yet" : value}</p>
      {hint ? <p className="hint">{hint}</p> : null}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="card empty-state">
      <h3>{title}</h3>
      {description ? <p>{description}</p> : null}
      {action ? <div style={{ marginTop: "1rem" }}>{action}</div> : null}
    </div>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return <div className="loading-center">{label}</div>;
}

export function ErrorBanner({ message }: { message: string }) {
  if (!message) return null;
  return <div className="banner banner-error">{message}</div>;
}

export function SuccessBanner({ message }: { message: string }) {
  if (!message) return null;
  return <div className="banner banner-ok">{message}</div>;
}

export function StatusBadge({
  tone = "muted",
  children,
}: {
  tone?: "ok" | "warn" | "danger" | "muted";
  children: ReactNode;
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function ComingSoonPage({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div>
      <PageHeader title={title} description={description} />
      <EmptyState
        title="Coming in a later phase"
        description="This module is reserved in the SaaS navigation. No demo data is shown here."
      />
    </div>
  );
}
