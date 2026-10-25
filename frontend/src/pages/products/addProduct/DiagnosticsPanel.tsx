import { useState } from "react";
import {
  formatDiagnosticsCopy,
  type DestinationAttemptLike,
  type StageHistoryEntry,
} from "@/lib/productAddProgress";

export function DiagnosticsPanel({
  correlationId,
  phase,
  message,
  destinations,
  stageHistory,
  elapsedMs,
  requestRunning,
}: {
  correlationId?: string | null;
  phase: string;
  message?: string;
  destinations?: DestinationAttemptLike[];
  stageHistory?: StageHistoryEntry[];
  elapsedMs?: number | null;
  requestRunning?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  async function onCopy() {
    const text = formatDiagnosticsCopy({
      correlationId,
      phase,
      message,
      destinations,
      stageHistory,
      elapsedMs,
    });
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="add-diagnostics">
      <button
        type="button"
        className="add-diagnostics__toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span>Activity &amp; diagnostics</span>
        <span className="muted-line">{open ? "Hide" : "Show"}</span>
      </button>
      {open ? (
        <div className="add-diagnostics__body">
          <div className="add-diagnostics__meta">
            <p>
              <strong>Status:</strong>{" "}
              {requestRunning ? "Request running" : phase === "error" ? "Failed" : "Idle / completed"}
            </p>
            <p>
              <strong>Diagnostic ID:</strong>{" "}
              {correlationId ? <code>{correlationId}</code> : "Not assigned yet"}
            </p>
            {message ? (
              <p>
                <strong>Message:</strong> {message}
              </p>
            ) : null}
          </div>

          {stageHistory && stageHistory.length > 0 ? (
            <ul className="add-diagnostics__stages">
              {stageHistory.map((s, i) => (
                <li key={`${s.stage}-${s.at_ms}-${i}`}>
                  <span className="muted-line">
                    {s.at_ms != null ? `+${Math.round(Number(s.at_ms))}ms` : "—"}
                  </span>{" "}
                  <strong>{String(s.stage || "stage")}</strong>
                  {s.status ? ` · ${s.status}` : ""}
                  {s.reason ? ` · ${String(s.reason)}` : ""}
                  {s.store_id ? ` · ${s.store_id}` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted-line">No server stage history yet. Run Fetch or Add to populate.</p>
          )}

          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void onCopy()}>
            {copied ? "Copied" : "Copy diagnostics"}
          </button>
        </div>
      ) : null}
    </section>
  );
}
