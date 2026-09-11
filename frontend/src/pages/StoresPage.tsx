import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { Api } from "@/lib/api";
import { Dialog } from "@/components/ui/Dialog";
import {
  EmptyState,
  ErrorBanner,
  PageHeader,
  StatCard,
  StatusBadge,
  SuccessBanner,
} from "@/components/ui/Primitives";
import {
  formatRelativeTime,
  statusLabel,
  statusTone,
  storeStatus,
  storeTitle,
  tokenDaysLeft,
} from "@/lib/storeHealth";
import type { StoreGroup, StoreView } from "@/types/api";

const LEGACY_KEY = "multistore_vendor_v1";
const IMPORTED_FLAG = "multistore_profiles_imported_v1";
const VIEW_PREF_KEY = "multistore_stores_view_v1";
const VIRTUAL_ALL = "__all__";

type ViewMode = "cards" | "table";
type FilterMode = "all" | "healthy" | "attention" | "group";
type SortMode = "name-asc" | "name-desc";

export function StoresPage() {
  const [stores, setStores] = useState<StoreView[]>([]);
  const [groups, setGroups] = useState<StoreGroup[]>([]);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyStoreId, setBusyStoreId] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const [highlightId, setHighlightId] = useState<string | null>(null);

  const [view, setView] = useState<ViewMode>(() => {
    const saved = localStorage.getItem(VIEW_PREF_KEY);
    return saved === "table" ? "table" : "cards";
  });
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterMode>("all");
  const [filterGroupId, setFilterGroupId] = useState("");
  const [sort, setSort] = useState<SortMode>("name-asc");
  const [menuOpen, setMenuOpen] = useState<string | null>(null);

  const [renameTarget, setRenameTarget] = useState<StoreView | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [groupEditor, setGroupEditor] = useState<StoreGroup | "new" | null>(null);
  const [groupName, setGroupName] = useState("");
  const [groupStoreIds, setGroupStoreIds] = useState<string[]>([]);

  const refresh = useCallback(async () => {
    const [s, g] = await Promise.all([Api.listStores(), Api.listGroups()]);
    setStores(s.stores || []);
    setGroups(g.groups || []);
    return s.stores || [];
  }, []);

  useEffect(() => {
    localStorage.setItem(VIEW_PREF_KEY, view);
  }, [view]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await refresh();
        if (cancelled) return;

        if (params.get("connected") === "1") {
          const sid = params.get("store");
          setOk("Store connected successfully");
          if (sid) setHighlightId(sid);
          const next = new URLSearchParams(params);
          next.delete("connected");
          next.delete("store");
          setParams(next, { replace: true });
        }
        if (params.get("oauth_error") === "1") {
          setError("Daraz connection failed. Try Connect Daraz Store again.");
          const next = new URLSearchParams(params);
          next.delete("oauth_error");
          next.delete("message");
          setParams(next, { replace: true });
        }

        if (localStorage.getItem(IMPORTED_FLAG) !== "1") {
          try {
            const raw = localStorage.getItem(LEGACY_KEY);
            const profiles = raw ? (JSON.parse(raw).profiles as Record<string, string[]>) : null;
            if (profiles && Object.keys(profiles).length) {
              const existing = (await Api.listGroups()).groups || [];
              if (existing.length === 0) {
                const should = window.confirm(
                  `Import ${Object.keys(profiles).length} browser profile(s) as store groups?`
                );
                if (should) {
                  await Api.importBrowserProfiles(profiles);
                  localStorage.setItem(IMPORTED_FLAG, "1");
                  await refresh();
                  setOk("Browser profiles imported as store groups");
                }
              } else {
                localStorage.setItem(IMPORTED_FLAG, "1");
              }
            }
          } catch {
            /* ignore */
          }
        }

        void list;
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load stores");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh, params, setParams]);

  useEffect(() => {
    if (!highlightId) return;
    const t = window.setTimeout(() => setHighlightId(null), 8000);
    return () => window.clearTimeout(t);
  }, [highlightId]);

  const groupMembership = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const g of groups) {
      for (const sid of g.store_ids) {
        const prev = map.get(sid) || [];
        prev.push(g.name);
        map.set(sid, prev);
      }
    }
    return map;
  }, [groups]);

  const knownAttention = useMemo(
    () => stores.filter((s) => s.needs_attention === true || storeStatus(s) === "needs_reconnection"),
    [stores]
  );
  const knownHealthy = useMemo(
    () => stores.filter((s) => s.needs_attention === false && storeStatus(s) === "connected"),
    [stores]
  );
  const unknownHealth = stores.length - knownHealthy.length - knownAttention.length;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let rows = [...stores];
    if (q) {
      rows = rows.filter((s) => {
        const hay = [
          storeTitle(s),
          s.shop_name,
          s.store_name,
          s.account,
          s.seller_id,
          s.store_id,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return hay.includes(q);
      });
    }
    if (filter === "healthy") {
      rows = rows.filter((s) => s.needs_attention === false && storeStatus(s) === "connected");
    } else if (filter === "attention") {
      rows = rows.filter(
        (s) => s.needs_attention === true || storeStatus(s) === "needs_reconnection"
      );
    } else if (filter === "group" && filterGroupId) {
      if (filterGroupId === VIRTUAL_ALL) {
        /* all stores — no extra filter */
      } else {
        const g = groups.find((x) => x.id === filterGroupId);
        const ids = new Set(g?.store_ids || []);
        rows = rows.filter((s) => ids.has(s.store_id));
      }
    }
    rows.sort((a, b) => {
      const an = storeTitle(a).toLowerCase();
      const bn = storeTitle(b).toLowerCase();
      return sort === "name-asc" ? an.localeCompare(bn) : bn.localeCompare(an);
    });
    return rows;
  }, [stores, query, filter, filterGroupId, groups, sort]);

  async function connectStore() {
    setError("");
    try {
      const data = await Api.oauthStart();
      window.location.href = data.authorize_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start OAuth");
    }
  }

  async function refreshOne(store: StoreView) {
    setError("");
    setBusyStoreId(store.store_id);
    setMenuOpen(null);
    try {
      const data = await Api.refreshTokens([store.store_id], true);
      const bad = (data.results || []).find((r) => r.status === "error");
      await refresh();
      if (bad) setError(bad.error || "Refresh failed — try Reconnect with Daraz");
      else setOk(`Connection refreshed for ${storeTitle(store)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    } finally {
      setBusyStoreId(null);
    }
  }

  function openRename(store: StoreView) {
    setMenuOpen(null);
    setRenameTarget(store);
    setRenameValue(store.display_name || store.store_name || "");
  }

  async function saveRename(e: FormEvent) {
    e.preventDefault();
    if (!renameTarget) return;
    const trimmed = renameValue.trim();
    if (!trimmed) {
      setError("Name cannot be empty");
      return;
    }
    setError("");
    try {
      await Api.renameStore(renameTarget.store_id, trimmed);
      setRenameTarget(null);
      await refresh();
      setOk(`Renamed to “${trimmed}”`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rename failed");
    }
  }

  function openNewGroup() {
    setGroupEditor("new");
    setGroupName("");
    setGroupStoreIds([]);
  }

  function openEditGroup(group: StoreGroup) {
    setGroupEditor(group);
    setGroupName(group.name);
    setGroupStoreIds([...group.store_ids]);
  }

  function openManageGroupsForStore(store: StoreView) {
    setMenuOpen(null);
    openNewGroup();
    setGroupStoreIds([store.store_id]);
  }

  async function saveGroup(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      if (!groupName.trim()) throw new Error("Enter a group name");
      if (!groupStoreIds.length) throw new Error("Select at least one store");
      if (groupEditor === "new") {
        await Api.createGroup(groupName.trim(), groupStoreIds);
        setOk("Store group created");
      } else if (groupEditor && typeof groupEditor === "object") {
        await Api.updateGroup(groupEditor.id, {
          name: groupName.trim(),
          store_ids: groupStoreIds,
        });
        setOk("Store group updated");
      }
      setGroupEditor(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save group");
    }
  }

  async function removeGroup(group: StoreGroup) {
    if (!window.confirm(`Delete group “${group.name}”?`)) return;
    try {
      await Api.deleteGroup(group.id);
      await refresh();
      setOk(`Deleted “${group.name}”`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  function storeActions(store: StoreView) {
    return (
      <div className="action-menu-wrap">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-expanded={menuOpen === store.store_id}
          onClick={() => setMenuOpen((id) => (id === store.store_id ? null : store.store_id))}
        >
          Actions
        </button>
        {menuOpen === store.store_id ? (
          <div className="action-menu">
            <button type="button" onClick={() => openRename(store)}>
              Rename
            </button>
            <button
              type="button"
              disabled={busyStoreId === store.store_id}
              onClick={() => refreshOne(store)}
            >
              Refresh Connection
            </button>
            <button type="button" onClick={() => { setMenuOpen(null); connectStore(); }}>
              Reconnect with Daraz
            </button>
            <button type="button" onClick={() => openManageGroupsForStore(store)}>
              Manage Groups
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="stack" onClick={() => menuOpen && setMenuOpen(null)}>
      <PageHeader
        title="Stores"
        description="Manage all Daraz stores connected to this workspace."
        actions={
          <button type="button" className="btn btn-accent" onClick={connectStore}>
            + Connect Daraz Store
          </button>
        }
      />
      <ErrorBanner message={error} />
      <SuccessBanner message={ok} />

      <div className="grid-stats">
        <StatCard label="Connected Stores" value={loading ? "…" : stores.length} hint="Registered in this workspace" />
        <StatCard
          label="Healthy Stores"
          value={loading ? "…" : knownHealthy.length}
          hint={
            unknownHealth > 0
              ? `${unknownHealth} store(s) have unknown token expiry`
              : "Access token healthy (>10 days)"
          }
        />
        <StatCard
          label="Needs Attention"
          value={loading ? "…" : knownAttention.length}
          hint="Expiring soon or needs reconnection"
        />
        <StatCard label="Store Groups" value={loading ? "…" : groups.length} hint="Server-side groups" />
      </div>

      <section className="card">
        <div className="toolbar stores-toolbar">
          <label className="field" style={{ margin: 0, flex: "1 1 200px" }}>
            <span className="sr-only">Search stores</span>
            <input
              value={query}
              placeholder="Search stores…"
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <label className="field" style={{ margin: 0, minWidth: 150 }}>
            <span>Filter</span>
            <select
              value={filter === "group" ? `group:${filterGroupId || VIRTUAL_ALL}` : filter}
              onChange={(e) => {
                const v = e.target.value;
                if (v.startsWith("group:")) {
                  setFilter("group");
                  setFilterGroupId(v.slice(6));
                } else {
                  setFilter(v as FilterMode);
                  setFilterGroupId("");
                }
              }}
            >
              <option value="all">All</option>
              <option value="healthy">Healthy</option>
              <option value="attention">Needs Attention</option>
              <option value={`group:${VIRTUAL_ALL}`}>Group: All Stores</option>
              {groups.map((g) => (
                <option key={g.id} value={`group:${g.id}`}>
                  Group: {g.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field" style={{ margin: 0, minWidth: 130 }}>
            <span>Sort</span>
            <select value={sort} onChange={(e) => setSort(e.target.value as SortMode)}>
              <option value="name-asc">Name A-Z</option>
              <option value="name-desc">Name Z-A</option>
            </select>
          </label>
          <div className="tabs" role="tablist" aria-label="View mode" style={{ marginBottom: 0 }}>
            <button
              type="button"
              className={`tab${view === "cards" ? " active" : ""}`}
              onClick={() => setView("cards")}
            >
              Cards
            </button>
            <button
              type="button"
              className={`tab${view === "table" ? " active" : ""}`}
              onClick={() => setView("table")}
            >
              Table
            </button>
          </div>
        </div>

        {loading ? <p style={{ color: "var(--muted)" }}>Loading…</p> : null}
        {!loading && stores.length === 0 ? (
          <EmptyState
            title="No stores connected"
            description="Connect a Daraz seller account via OAuth to start loading orders and labels."
            action={
              <button type="button" className="btn btn-primary" onClick={connectStore}>
                + Connect Daraz Store
              </button>
            }
          />
        ) : null}

        {!loading && stores.length > 0 && filtered.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>No stores match this search or filter.</p>
        ) : null}

        {!loading && view === "cards" && filtered.length > 0 ? (
          <div className="store-mgmt-grid">
            {filtered.map((s) => {
              const days = tokenDaysLeft(s);
              const members = groupMembership.get(s.store_id) || [];
              return (
                <article
                  key={s.store_id}
                  className={`store-mgmt-card${highlightId === s.store_id ? " highlight" : ""}`}
                  onClick={(e) => e.stopPropagation()}
                >
                  <div className="store-mgmt-card-top">
                    <div style={{ minWidth: 0 }}>
                      <h4>{storeTitle(s)}</h4>
                      {s.shop_name && s.shop_name !== storeTitle(s) ? (
                        <p className="muted-line">Daraz: {s.shop_name}</p>
                      ) : null}
                      {s.account ? <p className="muted-line">{s.account}</p> : null}
                    </div>
                    <StatusBadge tone={statusTone(s)}>{statusLabel(s)}</StatusBadge>
                  </div>
                  <div className="row" style={{ gap: "0.35rem", flexWrap: "wrap" }}>
                    <StatusBadge tone="muted">{(s.country || "pk").toUpperCase()}</StatusBadge>
                    {days != null ? (
                      <StatusBadge tone={days <= 3 ? "danger" : days <= 10 ? "warn" : "ok"}>
                        Token · {days}d
                      </StatusBadge>
                    ) : (
                      <StatusBadge tone="muted">Token expiry unknown</StatusBadge>
                    )}
                    {s.seller_id ? <StatusBadge tone="muted">Seller {s.seller_id}</StatusBadge> : null}
                  </div>
                  <p className="muted-line" style={{ marginTop: "0.55rem" }}>
                    Groups: {members.length ? members.join(", ") : "—"}
                  </p>
                  <p className="muted-line">
                    Updated {formatRelativeTime(s.updated_at || s.authorized_at)}
                  </p>
                  <div className="store-mgmt-card-actions" onClick={(e) => e.stopPropagation()}>
                    {storeActions(s)}
                  </div>
                </article>
              );
            })}
          </div>
        ) : null}

        {!loading && view === "table" && filtered.length > 0 ? (
          <div className="table-wrap stores-table-desktop" onClick={(e) => e.stopPropagation()}>
            <table className="data">
              <thead>
                <tr>
                  <th>Store</th>
                  <th>Status</th>
                  <th>Groups</th>
                  <th>Last Updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((s) => (
                  <tr key={s.store_id} className={highlightId === s.store_id ? "row-highlight" : ""}>
                    <td>
                      <strong>{storeTitle(s)}</strong>
                      {s.shop_name && s.shop_name !== storeTitle(s) ? (
                        <div className="muted-line">Daraz: {s.shop_name}</div>
                      ) : null}
                      {s.account ? <div className="muted-line">{s.account}</div> : null}
                    </td>
                    <td>
                      <StatusBadge tone={statusTone(s)}>{statusLabel(s)}</StatusBadge>
                    </td>
                    <td>{(groupMembership.get(s.store_id) || []).join(", ") || "—"}</td>
                    <td>{formatRelativeTime(s.updated_at || s.authorized_at)}</td>
                    <td>{storeActions(s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {/* Mobile stack mirrors cards when table selected on narrow screens via CSS */}
        {!loading && view === "table" && filtered.length > 0 ? (
          <div className="store-mgmt-grid stores-table-mobile">
            {filtered.map((s) => (
              <article
                key={`m-${s.store_id}`}
                className={`store-mgmt-card${highlightId === s.store_id ? " highlight" : ""}`}
                onClick={(e) => e.stopPropagation()}
              >
                <div className="store-mgmt-card-top">
                  <h4>{storeTitle(s)}</h4>
                  <StatusBadge tone={statusTone(s)}>{statusLabel(s)}</StatusBadge>
                </div>
                <p className="muted-line">
                  Groups: {(groupMembership.get(s.store_id) || []).join(", ") || "—"}
                </p>
                <p className="muted-line">Updated {formatRelativeTime(s.updated_at || s.authorized_at)}</p>
                <div className="store-mgmt-card-actions">{storeActions(s)}</div>
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <section className="card">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.75rem" }}>
          <h3 className="section-title" style={{ margin: 0 }}>
            Store Groups
          </h3>
          <button type="button" className="btn btn-primary btn-sm" onClick={openNewGroup} disabled={!stores.length}>
            + New Group
          </button>
        </div>
        <p style={{ marginTop: 0, color: "var(--muted)", fontSize: "0.9rem" }}>
          “All Stores” is always available in Shipping as a virtual selection — it is not a deletable group.
        </p>
        {groups.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>No store groups yet.</p>
        ) : (
          <ul className="group-list">
            {groups.map((g) => (
              <li key={g.id} className="group-list-item">
                <div>
                  <strong>{g.name}</strong>
                  <span className="muted-line">
                    {g.store_ids.length} store{g.store_ids.length === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="row">
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => openEditGroup(g)}>
                    Edit
                  </button>
                  <button type="button" className="btn btn-danger btn-sm" onClick={() => removeGroup(g)}>
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Dialog open={Boolean(renameTarget)} title="Rename Store" onClose={() => setRenameTarget(null)}>
        <form className="stack" onSubmit={saveRename}>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: "0.9rem" }}>
            MultiStore display name only. Daraz shop identity is unchanged.
          </p>
          <label className="field">
            <span>Display name</span>
            <input
              value={renameValue}
              maxLength={80}
              autoFocus
              onChange={(e) => setRenameValue(e.target.value)}
            />
          </label>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-ghost" onClick={() => setRenameTarget(null)}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary">
              Save
            </button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={groupEditor != null}
        title={groupEditor === "new" ? "New Group" : "Edit Group"}
        onClose={() => setGroupEditor(null)}
      >
        <form className="stack" onSubmit={saveGroup}>
          <label className="field">
            <span>Group name</span>
            <input
              value={groupName}
              maxLength={40}
              placeholder="e.g. High Volume"
              onChange={(e) => setGroupName(e.target.value)}
            />
          </label>
          <div className="store-grid">
            {stores.map((s) => {
              const checked = groupStoreIds.includes(s.store_id);
              return (
                <label key={s.store_id} className={`store-card${checked ? " selected" : ""}`}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      setGroupStoreIds((prev) =>
                        checked ? prev.filter((id) => id !== s.store_id) : [...prev, s.store_id]
                      );
                    }}
                  />
                  <strong>{storeTitle(s)}</strong>
                </label>
              );
            })}
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-ghost" onClick={() => setGroupEditor(null)}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary">
              Save Group
            </button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
