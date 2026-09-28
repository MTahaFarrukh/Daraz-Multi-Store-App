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
      {actions ? <div className="row page-header-actions">{actions}</div> : null}
    </div>
  );
}

export function SectionCard({
  title,
  description,
  children,
  actions,
}: {
  title?: string;
  description?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="card section-card">
      {(title || actions) && (
        <div className="row" style={{ justifyContent: "space-between", gap: "0.75rem", flexWrap: "wrap" }}>
          <div>
            {title ? <h3 className="section-title" style={{ marginBottom: description ? "0.25rem" : undefined }}>{title}</h3> : null}
            {description ? <p className="muted-line" style={{ margin: 0 }}>{description}</p> : null}
          </div>
          {actions ? <div className="row">{actions}</div> : null}
        </div>
      )}
      {children}
    </section>
  );
}

export function FilterBar({ children }: { children: ReactNode }) {
  return (
    <div className="card filter-bar">
      <div className="row filters-row" style={{ flexWrap: "wrap", gap: "0.75rem", alignItems: "end" }}>
        {children}
      </div>
    </div>
  );
}

export function PaginationBar({
  page,
  totalPages,
  total,
  label = "items",
  onPrev,
  onNext,
}: {
  page: number;
  totalPages: number;
  total?: number;
  label?: string;
  onPrev: () => void;
  onNext: () => void;
}) {
  return (
    <div className="row pagination-bar" style={{ justifyContent: "space-between", marginTop: "0.85rem", flexWrap: "wrap", gap: "0.5rem" }}>
      <span className="muted-line">
        {total != null ? `${total} ${label}` : null}
        {total != null ? " · " : null}
        page {page} of {Math.max(1, totalPages)}
      </span>
      <div className="row" style={{ gap: "0.5rem" }}>
        <button type="button" className="btn" disabled={page <= 1} onClick={onPrev} aria-label="Previous page">
          Previous
        </button>
        <button
          type="button"
          className="btn"
          disabled={page >= totalPages}
          onClick={onNext}
          aria-label="Next page"
        >
          Next
        </button>
      </div>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div className="dialog-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="dialog-panel card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="confirm-dialog-title" className="section-title">
          {title}
        </h3>
        <p className="muted-line">{message}</p>
        <div className="row" style={{ justifyContent: "flex-end", gap: "0.5rem", marginTop: "1rem" }}>
          <button type="button" className="btn" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  unavailable,
  title: tip,
}: {
  label: string;
  value: string | number;
  hint?: string;
  unavailable?: boolean;
  title?: string;
}) {
  return (
    <div className={`card stat-card${unavailable ? " unavailable" : ""}`} title={tip}>
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
  return (
    <div className="loading-center" role="status" aria-live="polite">
      {label}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  if (!message) return null;
  return (
    <div className="banner banner-error" role="alert">
      {message}
    </div>
  );
}

export function SuccessBanner({ message }: { message: string }) {
  if (!message) return null;
  return (
    <div className="banner banner-ok" role="status">
      {message}
    </div>
  );
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
