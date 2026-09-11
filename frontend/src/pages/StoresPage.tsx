import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { Api } from "@/lib/api";
import {
  EmptyState,
  ErrorBanner,
  PageHeader,
  SuccessBanner,
  StatusBadge,
} from "@/components/ui/Primitives";
import type { StoreGroup, StoreView } from "@/types/api";

const LEGACY_KEY = "multistore_vendor_v1";
const IMPORTED_FLAG = "multistore_profiles_imported_v1";

export function StoresPage() {
  const [stores, setStores] = useState<StoreView[]>([]);
  const [groups, setGroups] = useState<StoreGroup[]>([]);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [loading, setLoading] = useState(true);
  const [groupName, setGroupName] = useState("");
  const [groupStoreIds, setGroupStoreIds] = useState<string[]>([]);
  const [params] = useSearchParams();

  const refresh = useCallback(async () => {
    const [s, g] = await Promise.all([Api.listStores(), Api.listGroups()]);
    setStores(s.stores || []);
    setGroups(g.groups || []);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await refresh();
        if (params.get("connected") === "1") {
          setOk("Store connected successfully");
        }
        // Optional one-time browser profile import
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
            /* ignore import errors */
          }
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load stores");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh, params]);

  async function connectStore() {
    setError("");
    try {
      const data = await Api.oauthStart();
      window.location.href = data.authorize_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start OAuth");
    }
  }

  async function rename(store: StoreView) {
    const next = window.prompt(
      "Store display name:",
      store.display_name || store.store_name || ""
    );
    if (next == null) return;
    const trimmed = next.trim();
    if (!trimmed) {
      setError("Name cannot be empty");
      return;
    }
    try {
      await Api.renameStore(store.store_id, trimmed);
      await refresh();
      setOk(`Renamed to “${trimmed}”`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rename failed");
    }
  }

  async function refreshTokens() {
    if (!stores.length) return;
    try {
      const data = await Api.refreshTokens(stores.map((s) => s.store_id), true);
      const bad = (data.results || []).filter((r) => r.status === "error");
      await refresh();
      if (bad.length) setError(bad[0].error || "Refresh failed");
      else setOk("Tokens refreshed");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    }
  }

  async function saveGroup(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      if (!groupName.trim()) throw new Error("Enter a group name");
      if (!groupStoreIds.length) throw new Error("Select at least one store");
      await Api.createGroup(groupName.trim(), groupStoreIds);
      setGroupName("");
      setGroupStoreIds([]);
      await refresh();
      setOk("Store group saved");
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

  return (
    <div className="stack">
      <PageHeader
        title="Stores"
        description="Connected Daraz sellers in this workspace. Tokens stay encrypted on the server."
        actions={
          <>
            <button type="button" className="btn btn-ghost" onClick={refreshTokens} disabled={!stores.length}>
              Refresh tokens
            </button>
            <button type="button" className="btn btn-accent" onClick={connectStore}>
              Connect store
            </button>
          </>
        }
      />
      <ErrorBanner message={error} />
      <SuccessBanner message={ok} />

      <section className="card">
        <h3 className="section-title">Connected stores</h3>
        {loading ? <p style={{ color: "var(--muted)" }}>Loading…</p> : null}
        {!loading && stores.length === 0 ? (
          <EmptyState
            title="No stores connected"
            description="Connect a Daraz seller account via OAuth to start loading orders and labels."
            action={
              <button type="button" className="btn btn-primary" onClick={connectStore}>
                Connect store
              </button>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Account</th>
                  <th>Country</th>
                  <th>Token</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {stores.map((s) => {
                  const days =
                    s.access_token_expires_in_seconds != null
                      ? Math.max(0, Math.round(s.access_token_expires_in_seconds / 86400))
                      : null;
                  return (
                    <tr key={s.store_id}>
                      <td>{s.display_name || s.store_name || s.store_id}</td>
                      <td>{s.account || "—"}</td>
                      <td>{(s.country || "pk").toUpperCase()}</td>
                      <td>
                        <StatusBadge
                          tone={
                            days == null ? "muted" : days <= 3 ? "danger" : days <= 10 ? "warn" : "ok"
                          }
                        >
                          {days == null ? "Unknown" : `${days}d left`}
                        </StatusBadge>
                      </td>
                      <td>
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => rename(s)}>
                          Rename
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h3 className="section-title">Store groups</h3>
        <p style={{ marginTop: 0, color: "var(--muted)", fontSize: "0.9rem" }}>
          Server-side groups replace the old browser-only “Saved profile” concept.
        </p>
        <form className="stack" onSubmit={saveGroup}>
          <label className="field">
            <span>Group name</span>
            <input
              value={groupName}
              maxLength={40}
              placeholder="e.g. Vendor Ali"
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
                  <strong>{s.display_name || s.store_id}</strong>
                </label>
              );
            })}
          </div>
          <button type="submit" className="btn btn-primary" disabled={!stores.length}>
            Save group
          </button>
        </form>

        {groups.length > 0 ? (
          <div className="table-wrap" style={{ marginTop: "1rem" }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Stores</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {groups.map((g) => (
                  <tr key={g.id}>
                    <td>{g.name}</td>
                    <td>{g.store_ids.length}</td>
                    <td>
                      <button type="button" className="btn btn-danger btn-sm" onClick={() => removeGroup(g)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p style={{ color: "var(--muted)" }}>No store groups yet.</p>
        )}
      </section>
    </div>
  );
}
