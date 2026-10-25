import { StatusBadge } from "@/components/ui/Primitives";
import {
  canContinueDeepScan,
  deepScanProgressLabel,
  formatCatalogEvidenceLines,
  formatNoBrandCheckLine,
  noBrandCheckTone,
  type NoBrandCheckResult,
} from "@/lib/noBrandCheck";
import {
  destinationActionKind,
  destinationProgressLabel,
  destinationReasonLabel,
  destinationRecoveryHint,
  destinationStatusTone,
  destinationUiStatus,
  type DestinationAttemptLike,
} from "@/lib/productAddProgress";

export function DestinationStatusCard({
  dest,
  processing,
  checking,
  onRetry,
  onReconcile,
  actionsDisabled,
  retryDisabled,
  noBrand,
  noBrandChecking,
  noBrandDisabled,
  noBrandDisabledReason,
  onCheckNoBrand,
  onDeepScanNoBrand,
  onCancelDeepScan,
  deepScanActive,
  deepScanPaused,
}: {
  dest: DestinationAttemptLike;
  processing?: boolean;
  checking?: boolean;
  onRetry?: () => void;
  onReconcile?: () => void;
  actionsDisabled?: boolean;
  /** When create gate is off, hide live Retry (Reconcile remains allowed). */
  retryDisabled?: boolean;
  noBrand?: NoBrandCheckResult | null;
  noBrandChecking?: boolean;
  noBrandDisabled?: boolean;
  noBrandDisabledReason?: string;
  onCheckNoBrand?: () => void;
  onDeepScanNoBrand?: () => void;
  onCancelDeepScan?: () => void;
  deepScanActive?: boolean;
  deepScanPaused?: boolean;
}) {
  const ui = destinationUiStatus(dest, { processing, checking });
  const tone = destinationStatusTone(ui);
  const reasonRaw = String(dest.reason || "").trim();
  const reasonText = reasonRaw
    ? destinationReasonLabel(reasonRaw)
    : ui === "attention" || ui === "failed"
      ? "Reason unavailable"
      : "";
  const recovery = reasonText ? destinationRecoveryHint(dest) : "";
  const action = destinationActionKind(dest);
  const name = dest.store?.display_name || dest.store?.store_id || "Destination store";
  const brandTone = noBrand ? noBrandCheckTone(noBrand.status) : "muted";

  return (
    <article className={`add-dest-card add-dest-card--${ui}`}>
      <div className="add-dest-card__top">
        <div className="add-dest-card__identity">
          <div className="add-dest-card__avatar" aria-hidden>
            {(name || "S").slice(0, 1).toUpperCase()}
          </div>
          <div>
            <h4 className="add-dest-card__name">{name}</h4>
            <p className="muted-line" style={{ margin: 0 }}>
              {dest.store?.store_id || "Selected destination"}
            </p>
          </div>
        </div>
        <StatusBadge tone={tone}>{destinationProgressLabel(dest)}</StatusBadge>
      </div>

      {reasonText ? (
        <div className="add-dest-card__reason">
          <p className="add-dest-card__reason-label">Why</p>
          <p className="add-dest-card__reason-text">{reasonText}</p>
          {recovery ? <p className="add-dest-card__next">{recovery}</p> : null}
        </div>
      ) : null}

      {dest.attempt_id || dest.item_id ? (
        <p className="muted-line add-dest-card__meta">
          {dest.attempt_id ? `Attempt ${dest.attempt_id}` : ""}
          {dest.attempt_id && dest.item_id ? " · " : ""}
          {dest.item_id ? `Item ${dest.item_id}` : ""}
        </p>
      ) : null}

      {onCheckNoBrand ? (
        <div className="stack" style={{ gap: "0.35rem", marginTop: "0.65rem" }}>
          <div className="row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={Boolean(noBrandDisabled) || Boolean(noBrandChecking) || actionsDisabled}
              title={
                noBrandDisabled
                  ? noBrandDisabledReason || "primary_category_id required from preview"
                  : "Read-only GET — does not create or migrate"
              }
              onClick={onCheckNoBrand}
            >
              {noBrandChecking && !deepScanActive
                ? "Checking No Brand…"
                : "Check No Brand (read-only)"}
            </button>
            {onDeepScanNoBrand ? (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={
                  Boolean(noBrandDisabled) ||
                  Boolean(noBrandChecking) ||
                  actionsDisabled ||
                  noBrand?.status === "NO_BRAND"
                }
                title="Resumable read-only deep scan — never CreateProduct"
                onClick={onDeepScanNoBrand}
              >
                {deepScanActive
                  ? deepScanPaused
                    ? "Resume deep scan"
                    : "Deep scanning…"
                  : canContinueDeepScan(noBrand)
                    ? "Continue deep scan"
                    : "Deep scan No Brand"}
              </button>
            ) : null}
            {deepScanActive && onCancelDeepScan ? (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={onCancelDeepScan}
              >
                Pause / cancel
              </button>
            ) : null}
          </div>
          {noBrandDisabled && noBrandDisabledReason ? (
            <p className="muted-line" style={{ margin: 0 }}>
              {noBrandDisabledReason}
            </p>
          ) : null}
          {noBrand ? (
            <div className="add-dest-card__reason">
              <p className="add-dest-card__reason-label">No Brand check</p>
              <p className="add-dest-card__reason-text">
                <StatusBadge tone={brandTone}>{noBrand.status}</StatusBadge>
                {noBrand.status !== "NO_BRAND" ? (
                  <span className="muted-line">
                    {" "}
                    · Create blocked until brands API returns NO_BRAND (catalog evidence is
                    informational only)
                  </span>
                ) : null}
              </p>
              <p className="muted-line" style={{ margin: "0.25rem 0 0" }}>
                {formatNoBrandCheckLine(noBrand)}
              </p>
              {deepScanProgressLabel(noBrand) ? (
                <p className="muted-line" style={{ margin: "0.15rem 0 0" }}>
                  Deep scan: {deepScanProgressLabel(noBrand)}
                </p>
              ) : null}
              {noBrand.brand_id != null && noBrand.brand_id !== "" ? (
                <p className="muted-line" style={{ margin: "0.15rem 0 0" }}>
                  brand_id={String(noBrand.brand_id)}
                </p>
              ) : null}
              {noBrand.catalog_consistency_note ? (
                <p className="muted-line" style={{ margin: "0.15rem 0 0" }}>
                  {noBrand.catalog_consistency_note}
                </p>
              ) : null}
              {noBrand.seller_item_limit ? (
                <p className="muted-line" style={{ margin: "0.15rem 0 0" }}>
                  Item limit: {JSON.stringify(noBrand.seller_item_limit)}
                </p>
              ) : null}
              {noBrand.image_preflight ? (
                <p className="muted-line" style={{ margin: "0.15rem 0 0" }}>
                  Image preflight: {JSON.stringify(noBrand.image_preflight)}
                </p>
              ) : null}
              {noBrand.catalog_evidence ? (
                <div style={{ marginTop: "0.45rem" }}>
                  <p className="add-dest-card__reason-label">Local catalog evidence</p>
                  {formatCatalogEvidenceLines(noBrand.catalog_evidence).map((line) => (
                    <p key={line} className="muted-line" style={{ margin: "0.15rem 0 0" }}>
                      {line}
                    </p>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {action !== "none" ? (
        <div className="row" style={{ gap: "0.5rem", marginTop: "0.65rem" }}>
          {action === "retry" && !retryDisabled ? (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={actionsDisabled}
              onClick={onRetry}
            >
              Retry (safe)
            </button>
          ) : null}
          {action === "retry" && retryDisabled ? (
            <p className="muted-line" style={{ margin: 0 }}>
              Retry blocked — product create gate is off
            </p>
          ) : null}
          {action === "reconcile" ? (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={actionsDisabled}
              onClick={onReconcile}
            >
              Reconcile
            </button>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
