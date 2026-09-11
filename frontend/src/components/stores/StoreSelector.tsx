import type { StoreGroup, StoreView } from "@/types/api";
import { StatusBadge } from "@/components/ui/Primitives";
import { statusLabel, statusTone, storeTitle, tokenDaysLeft } from "@/lib/storeHealth";

/** Virtual Shipping selection — not a persisted DB group. */
export const VIRTUAL_ALL_STORES = "__all__";

function tokenLabel(store: StoreView): string {
  const days = tokenDaysLeft(store);
  if (days == null) return "Token unknown";
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
  const validIds = new Set(stores.map((s) => s.store_id));

  return (
    <div className="stack">
      <div className="toolbar">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => {
            onChange(stores.map((s) => s.store_id));
            onGroupChange?.(VIRTUAL_ALL_STORES);
          }}
        >
          All
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => {
            onChange([]);
            onGroupChange?.("");
          }}
        >
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
              onChange={(e) => {
                const id = e.target.value;
                onGroupChange(id);
                if (!id) return;
                if (id === VIRTUAL_ALL_STORES) {
                  onChange(stores.map((s) => s.store_id));
                  return;
                }
                const g = groups.find((x) => x.id === id);
                if (!g) {
                  onChange([]);
                  return;
                }
                // Only currently registered members — empty group ≠ all stores
                onChange(g.store_ids.filter((sid) => validIds.has(sid)));
              }}
            >
              <option value="">— Custom selection —</option>
              <option value={VIRTUAL_ALL_STORES}>All Stores</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name} ({g.store_ids.filter((sid) => validIds.has(sid)).length})
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
                    onGroupChange?.("");
                    if (checked) onChange(selected.filter((id) => id !== store.store_id));
                    else onChange([...selected, store.store_id]);
                  }}
                />
                <div style={{ minWidth: 0 }}>
                  <strong style={{ display: "block" }}>{storeTitle(store)}</strong>
                  {store.account && store.account !== storeTitle(store) ? (
                    <div style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
                      {store.account}
                    </div>
                  ) : null}
                  <div className="row" style={{ marginTop: "0.35rem", gap: "0.35rem" }}>
                    <StatusBadge tone="muted">
                      {(store.country || "pk").toUpperCase()}
                    </StatusBadge>
                    <StatusBadge tone={statusTone(store)}>{statusLabel(store)}</StatusBadge>
                    <StatusBadge
                      tone={
                        tokenDaysLeft(store) == null
                          ? "muted"
                          : (tokenDaysLeft(store) || 0) <= 3
                            ? "danger"
                            : (tokenDaysLeft(store) || 0) <= 10
                              ? "warn"
                              : "ok"
                      }
                    >
                      {tokenLabel(store)}
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
