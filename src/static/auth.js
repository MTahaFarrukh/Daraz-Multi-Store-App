/**
 * Supabase session helpers for MultiStore Phase 1A.
 * Config is injected via /api/public-config (publishable key only).
 */
(() => {
  const SESSION_WORKSPACE_KEY = "multistore_workspace_id";
  let client = null;
  let publicConfig = null;

  async function loadConfig() {
    if (publicConfig) return publicConfig;
    const res = await fetch("/api/public-config");
    publicConfig = await res.json();
    return publicConfig;
  }

  async function ready() {
    const cfg = await loadConfig();
    if (!cfg.supabase_url || !cfg.supabase_publishable_key) {
      return false;
    }
    if (!window.supabase) {
      throw new Error("Supabase JS failed to load");
    }
    client = window.supabase.createClient(cfg.supabase_url, cfg.supabase_publishable_key, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
        storageKey: "multistore_supabase_auth",
      },
    });
    return true;
  }

  async function getSession() {
    if (!client) await ready();
    if (!client) return null;
    const { data, error } = await client.auth.getSession();
    if (error) throw error;
    return data.session || null;
  }

  async function getAccessToken() {
    const session = await getSession();
    return session?.access_token || null;
  }

  async function signIn(email, password) {
    if (!client) await ready();
    const { data, error } = await client.auth.signInWithPassword({ email, password });
    if (error) throw error;
    return data;
  }

  async function signUp(email, password) {
    if (!client) await ready();
    const { data, error } = await client.auth.signUp({ email, password });
    if (error) throw error;
    if (!data.session) {
      throw new Error(
        "Check your email to confirm the account, then sign in. " +
          "(Or disable email confirmations in Supabase Auth settings for local testing.)"
      );
    }
    return data;
  }

  async function signOut() {
    if (!client) await ready();
    if (client) await client.auth.signOut();
    localStorage.removeItem(SESSION_WORKSPACE_KEY);
  }

  async function bootstrapWorkspace() {
    const token = await getAccessToken();
    if (!token) throw new Error("Not signed in");
    const res = await fetch("/api/bootstrap", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.message || "Could not create workspace");
    }
    const wid = data.workspace?.id;
    if (wid) localStorage.setItem(SESSION_WORKSPACE_KEY, wid);
    return data;
  }

  function getWorkspaceId() {
    return localStorage.getItem(SESSION_WORKSPACE_KEY) || "";
  }

  function setWorkspaceId(id) {
    if (id) localStorage.setItem(SESSION_WORKSPACE_KEY, id);
  }

  async function api(url, options = {}) {
    const token = await getAccessToken();
    if (!token) {
      location.replace("/login");
      throw new Error("Authentication required");
    }
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${token}`);
    const wid = getWorkspaceId();
    if (wid) headers.set("X-Workspace-Id", wid);
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const res = await fetch(url, { ...options, headers });
    let data = null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      data = await res.json();
    } else {
      data = { detail: await res.text() };
    }
    if (res.status === 401) {
      location.replace("/login");
      throw new Error("Session expired — sign in again");
    }
    if (!res.ok) {
      const detail = data?.detail;
      const msg =
        typeof detail === "string"
          ? detail
          : detail?.message || detail?.error || res.statusText;
      throw new Error(msg || "Request failed");
    }
    return data;
  }

  window.MultiStoreAuth = {
    ready,
    getSession,
    getAccessToken,
    signIn,
    signUp,
    signOut,
    bootstrapWorkspace,
    getWorkspaceId,
    setWorkspaceId,
    api,
    SESSION_WORKSPACE_KEY,
  };
})();
