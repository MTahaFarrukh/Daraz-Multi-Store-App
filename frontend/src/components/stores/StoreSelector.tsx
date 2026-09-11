import type { StoreGroup, StoreView } from "@/types/api";
import { StatusBadge } from "@/components/ui/Primitives";

function tokenTone(seconds?: number | null): "ok" | "warn" | "danger" | "muted" {
  if (seconds == null) return "muted";
  const days = Math.max(0, Math.round(seconds / 86400));
  if (days <= 3) return "danger";
  if (days <= 10) return "warn";
  return "ok";
}

function tokenLabel(seconds?: number | null): string {
  if (seconds == null) return "Token unknown";
  const days = Math.max(0, Math.round(seconds / 86400));
  return `Token · ${days}d left`;
}

export function StoreSelector({
  stores,
  selected,
  onChange,
  groups,
  activeGroupId,
  onGroupChange,
}: {
  stores: StoreView[];
  selected: string[];
  onChange: (ids: string[]) => void;
  groups?: StoreGroup[];
  activeGroupId?: string;
  onGroupChange?: (groupId: string) => void;
}) {
  const selectedSet = new Set(selected);

  return (
    <div className="stack">
      <div className="toolbar">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => onChange(stores.map((s) => s.store_id))}
        >
          All
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange([])}>
          None
        </button>
        <span style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
          {selected.length} of {stores.length} selected
        </span>
        {groups && onGroupChange ? (
          <label className="field" style={{ margin: 0, minWidth: 180 }}>
            <span>Store group</span>
            <select
              value={activeGroupId || ""}
              onChange={(e) => onGroupChange(e.target.value)}
            >
              <option value="">— Custom selection —</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      {stores.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>No connected stores yet.</p>
      ) : (
        <div className="store-grid">
          {stores.map((store) => {
            const checked = selectedSet.has(store.store_id);
            return (
              <label
                key={store.store_id}
                className={`store-card${checked ? " selected" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => {
                    if (checked) onChange(selected.filter((id) => id !== store.store_id));
                    else onChange([...selected, store.store_id]);
                  }}
                />
                <div style={{ minWidth: 0 }}>
                  <strong style={{ display: "block" }}>
                    {store.display_name || store.store_name || store.store_id}
                  </strong>
                  {store.account && store.account !== store.display_name ? (
                    <div style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
                      {store.account}
                    </div>
                  ) : null}
                  <div className="row" style={{ marginTop: "0.35rem", gap: "0.35rem" }}>
                    <StatusBadge tone="muted">
                      {(store.country || "pk").toUpperCase()}
                    </StatusBadge>
                    <StatusBadge tone={tokenTone(store.access_token_expires_in_seconds)}>
                      {tokenLabel(store.access_token_expires_in_seconds)}
                    </StatusBadge>
                  </div>
                </div>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
