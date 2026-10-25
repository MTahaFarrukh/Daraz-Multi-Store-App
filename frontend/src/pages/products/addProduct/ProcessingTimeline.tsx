import { buildClientTimeline, type ClientStageId } from "@/lib/productAddProgress";

export function ProcessingTimeline({
  phase,
  hasResult,
  executedCreate,
  elapsedMs,
}: {
  phase: "idle" | "fetching" | "preparing" | "creating" | "done" | "error";
  hasResult: boolean;
  executedCreate: boolean;
  elapsedMs?: number | null;
}) {
  const steps = buildClientTimeline({ phase, hasResult, executedCreate });

  return (
    <section className="add-timeline" aria-label="Processing timeline">
      <div className="add-timeline__head">
        <h4>Progress</h4>
        {elapsedMs != null && phase !== "idle" ? (
          <span className="muted-line">{Math.round(elapsedMs / 1000)}s elapsed</span>
        ) : null}
      </div>
      <ol className="add-timeline__list">
        {steps.map((step) => (
          <li key={step.id} className={`add-timeline__step add-timeline__step--${step.state}`}>
            <span className="add-timeline__marker" aria-hidden>
              {marker(step.state)}
            </span>
            <span className="add-timeline__label">{step.label}</span>
            <span className="add-timeline__state">{stateLabel(step.id, step.state)}</span>
          </li>
        ))}
      </ol>
      {phase === "creating" ? (
        <p className="muted-line" style={{ margin: "0.5rem 0 0" }}>
          Create request in progress — do not refresh or submit again.
        </p>
      ) : null}
    </section>
  );
}

function marker(state: string): string {
  if (state === "done") return "✓";
  if (state === "active") return "●";
  if (state === "skipped") return "–";
  return "○";
}

function stateLabel(id: ClientStageId, state: string): string {
  if (state === "skipped" && id === "create") return "Not started";
  if (state === "done") return "Done";
  if (state === "active") return "Now";
  return "Waiting";
}
