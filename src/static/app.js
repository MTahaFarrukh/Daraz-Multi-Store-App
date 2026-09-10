(() => {
  const $ = (id) => document.getElementById(id);

  const storeList = $("store-list");
  const storeCount = $("store-count");
  const storeSelectionMeta = $("store-selection-meta");
  const btnSelectAll = $("btn-select-all");
  const btnSelectNone = $("btn-select-none");
  const profileSelect = $("profile-select");
  const profileName = $("profile-name");
  const btnSaveProfile = $("btn-save-profile");
  const btnDeleteProfile = $("btn-delete-profile");
  const limitSelect = $("limit-select");
  const ordersBody = $("orders-body");
  const ordersMeta = $("orders-meta");
  const btnRefresh = $("btn-refresh");
  const btnPrint = $("btn-print");
  const btnDownload = $("btn-download");
  const btnConnect = $("btn-connect");
  const btnLogout = $("btn-logout");
  const userMeta = $("user-meta");
  const form = $("controls-form");
  const toast = $("toast");
  const busy = $("busy");
  const busyText = $("busy-text");
  const labelSourcesPanel = $("label-sources-panel");
  const labelSourcesMeta = $("label-sources-meta");
  const labelSourcesBody = $("label-sources-body");

  const required = {
    "store-list": storeList,
    "store-count": storeCount,
    "btn-refresh": btnRefresh,
    "btn-print": btnPrint,
    "controls-form": form,
    "orders-body": ordersBody,
    "toast": toast,
  };
  const missing = Object.entries(required).filter(([, el]) => !el).map(([id]) => id);
  if (missing.length) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      `<div style="margin:1rem;padding:1rem;background:#fee;border:1px solid #c00;border-radius:8px">
        Dashboard UI is out of date. Hard refresh (Ctrl+F5) or redeploy. Missing: ${missing.join(", ")}
      </div>`
    );
    return;
  }

  const STORAGE_KEY = "multistore_vendor_v1";
  let allStores = [];
  let serverGroups = [];
  let toastTimer = null;
  let currentJobId = null;

  function loadVendorState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return { selection: [], profiles: {} };
      const parsed = JSON.parse(raw);
      return {
        selection: Array.isArray(parsed.selection) ? parsed.selection : [],
        profiles:
          parsed.profiles && typeof parsed.profiles === "object" ? parsed.profiles : {},
      };
    } catch {
      return { selection: [], profiles: {} };
    }
  }

  function saveVendorState(state) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function getSelection() {
    const checked = storeList.querySelectorAll('input[type="checkbox"][data-store-id]:checked');
    return Array.from(checked).map((el) => el.dataset.storeId || "").filter(Boolean);
  }

  function setSelection(storeIds, { persist = true } = {}) {
    const wanted = new Set(storeIds);
    for (const input of storeList.querySelectorAll('input[type="checkbox"][data-store-id]')) {
      input.checked = wanted.has(input.dataset.storeId);
      input.closest(".store-chip")?.classList.toggle("selected", input.checked);
    }
    updateSelectionMeta();
    if (persist) {
      const state = loadVendorState();
      state.selection = getSelection();
      saveVendorState(state);
    }
  }

  function updateSelectionMeta() {
    const selected = getSelection().length;
    const total = allStores.length;
    if (storeSelectionMeta) {
      storeSelectionMeta.textContent =
        total === 0 ? "0 selected" : `${selected} of ${total} selected`;
    }
    if (btnPrint) btnPrint.disabled = selected === 0;
    const loadBtn = form?.querySelector("#btn-load");
    if (loadBtn) loadBtn.disabled = selected === 0;
  }

  function showToast(message, isError = false) {
    toast.textContent = message;
    toast.classList.toggle("error", isError);
    toast.classList.remove("hidden");
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.classList.remove("show");
      setTimeout(() => toast.classList.add("hidden"), 250);
    }, 4200);
  }

  function setBusy(on, text = "Working…") {
    if (busyText) busyText.textContent = text;
    if (busy) {
      busy.classList.toggle("hidden", !on);
      busy.setAttribute("aria-hidden", on ? "false" : "true");
    }
  }

  async function api(url, options = {}) {
    return MultiStoreAuth.api(url, options);
  }

  function storeLabel(s) {
    return s.display_name || s.store_name || s.account || s.store_id || "Store";
  }

  async function renameStore(storeId, currentName) {
    const next = window.prompt("Store display name:", currentName);
    if (next === null) return;
    const trimmed = next.trim();
    if (!trimmed) {
      showToast("Name cannot be empty", true);
      return;
    }
    await api(`/api/stores/${encodeURIComponent(storeId)}`, {
      method: "PATCH",
      body: JSON.stringify({ display_name: trimmed }),
    });
    await loadStores();
    showToast(`Renamed to “${trimmed}”`);
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function loadServerGroups() {
    const data = await api("/api/store-groups");
    serverGroups = data.groups || [];
    renderProfileOptions();
  }

  function renderProfileOptions() {
    if (!profileSelect) return;
    const current = profileSelect.value;
    profileSelect.innerHTML = '<option value="">— Custom selection —</option>';
    for (const g of serverGroups) {
      const opt = document.createElement("option");
      opt.value = g.id;
      opt.textContent = g.name;
      profileSelect.appendChild(opt);
    }
    if (current && serverGroups.some((g) => g.id === current)) {
      profileSelect.value = current;
    }
  }

  async function maybeImportBrowserProfiles() {
    const state = loadVendorState();
    const names = Object.keys(state.profiles || {});
    if (!names.length) return;
    if (localStorage.getItem("multistore_profiles_imported_v1") === "1") return;
    if (serverGroups.length > 0) {
      localStorage.setItem("multistore_profiles_imported_v1", "1");
      return;
    }
    const ok = window.confirm(
      `Import ${names.length} browser profile(s) to your workspace? Local copies stay until import succeeds.`
    );
    if (!ok) return;
    const data = await api("/api/store-groups/import-browser", {
      method: "POST",
      body: JSON.stringify({ profiles: state.profiles }),
    });
    localStorage.setItem("multistore_profiles_imported_v1", "1");
    await loadServerGroups();
    showToast(`Imported ${(data.imported || []).length} profile(s)`);
  }

  function renderStores(stores) {
    allStores = stores;
    storeList.innerHTML = "";

    if (!stores.length) {
      storeCount.textContent = "0 connected";
      storeList.innerHTML =
        '<div class="store-chip empty">No store yet — click Connect store</div>';
      btnRefresh.disabled = true;
      btnPrint.disabled = true;
      updateSelectionMeta();
      return;
    }

    storeCount.textContent = `${stores.length} connected`;
    btnRefresh.disabled = false;

    const state = loadVendorState();
    const validIds = new Set(stores.map((s) => s.store_id));
    let initialSelection = state.selection.filter((id) => validIds.has(id));
    if (!initialSelection.length) {
      initialSelection = stores.map((s) => s.store_id).filter(Boolean);
    }

    for (const s of stores) {
      const sid = s.store_id || "";
      const chip = document.createElement("div");
      chip.className = "store-chip";
      const days =
        s.access_token_expires_in_seconds != null
          ? Math.max(0, Math.round(s.access_token_expires_in_seconds / 86400))
          : "?";
      const checked = initialSelection.includes(sid);
      const name = storeLabel(s);
      const showEmail = s.account && s.account !== name;
      if (checked) chip.classList.add("selected");
      chip.innerHTML = `
        <input type="checkbox" data-store-id="${escapeHtml(sid)}" ${checked ? "checked" : ""} aria-label="Select ${escapeHtml(name)}" />
        <div class="store-chip-body">
          <div class="store-chip-title">
            <strong class="store-chip-name">${escapeHtml(name)}</strong>
            <button type="button" class="btn-rename" title="Rename store" aria-label="Rename store">Rename</button>
          </div>
          ${showEmail ? `<span class="store-email">${escapeHtml(s.account)}</span>` : ""}
          <span class="store-chip-meta">
            <span class="store-country">${escapeHtml(s.country || "pk").toUpperCase()}</span>
            <span class="token-status ${days === "?" ? "token-unknown" : days <= 3 ? "token-critical" : days <= 10 ? "token-warn" : "token-ok"}">Token · ${days}d left</span>
          </span>
        </div>`;
      const input = chip.querySelector("input");
      chip.querySelector(".btn-rename")?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        renameStore(sid, name).catch((err) => showToast(err.message || "Rename failed", true));
      });
      chip.addEventListener("click", (event) => {
        if (event.target.closest(".btn-rename")) return;
        if (event.target === input) return;
        input.checked = !input.checked;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      });
      input?.addEventListener("change", () => {
        chip.classList.toggle("selected", input.checked);
        if (profileSelect) profileSelect.value = "";
        updateSelectionMeta();
        const next = loadVendorState();
        next.selection = getSelection();
        saveVendorState(next);
      });
      storeList.appendChild(chip);
    }

    updateSelectionMeta();
  }

  function requireSelectedStores() {
    const ids = getSelection();
    if (!ids.length) {
      throw new Error("Select at least one store above");
    }
    return ids;
  }

  function storeQueryParams(extra = {}) {
    const ids = requireSelectedStores();
    const qs = new URLSearchParams(extra);
    qs.set("stores", ids.join(","));
    return qs;
  }

  function renderOrders(orders) {
    ordersBody.innerHTML = "";
    if (!orders.length) {
      ordersMeta.textContent = "0 orders";
      ordersBody.innerHTML =
        '<tr class="empty-row"><td colspan="5">No ready-to-ship orders for selected stores.</td></tr>';
      return;
    }
    ordersMeta.textContent = `${orders.length} order${orders.length === 1 ? "" : "s"}`;
    for (const o of orders) {
      const tr = document.createElement("tr");
      const statuses = Array.isArray(o.statuses) ? o.statuses.join(", ") : o.statuses || "—";
      tr.innerHTML = `
        <td>${escapeHtml(o.store_name || o.display_name || o.store_id || "—")}</td>
        <td>${escapeHtml(String(o.order_id ?? "—"))}</td>
        <td>${escapeHtml(String(o.items_count ?? "—"))}</td>
        <td><span class="status-pill">${escapeHtml(statuses)}</span></td>
        <td>${escapeHtml(o.created_at || "—")}</td>`;
      ordersBody.appendChild(tr);
    }
  }

  function renderLabelSources(data) {
    if (!labelSourcesPanel || !labelSourcesBody) return;
    const details = data.label_details || [];
    if (!details.length) {
      labelSourcesPanel.classList.add("hidden");
      return;
    }

    labelSourcesPanel.classList.remove("hidden");
    labelSourcesMeta.textContent = `${details.length} label${details.length === 1 ? "" : "s"}`;

    labelSourcesBody.innerHTML = "";
    for (const row of details) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(row.store_name || "—")}</td>
        <td>${escapeHtml(String(row.order_id ?? "—"))}</td>`;
      labelSourcesBody.appendChild(tr);
    }
  }

  async function loadStores() {
    const data = await api("/api/stores");
    if (data.workspace_id) MultiStoreAuth.setWorkspaceId(data.workspace_id);
    renderStores(data.stores || []);
  }

  async function loadOrders(event) {
    if (event) event.preventDefault();
    const qs = storeQueryParams({
      limit: limitSelect.value,
      status: "ready_to_ship",
    });
    const selected = getSelection().length;
    setBusy(true, `Loading orders (${selected} store${selected === 1 ? "" : "s"})…`);
    try {
      const data = await api(`/api/orders?${qs}`);
      renderOrders(data.orders || []);
      showToast(`Loaded ${data.count || 0} orders from ${selected} store(s)`);
    } catch (err) {
      showToast(err.message || "Failed to load orders", true);
    } finally {
      setBusy(false);
    }
  }

  async function refreshTokens() {
    const qs = storeQueryParams({ force: "true" });
    const selected = getSelection().length;
    setBusy(true, `Refreshing tokens (${selected} store${selected === 1 ? "" : "s"})…`);
    try {
      const data = await api(`/api/refresh-tokens?${qs}`, { method: "POST" });
      const bad = (data.results || []).filter((r) => r.status === "error");
      await loadStores();
      if (bad.length) {
        showToast(bad[0].error || "Refresh failed", true);
      } else {
        showToast("Tokens refreshed for selected stores");
      }
    } catch (err) {
      showToast(err.message || "Refresh failed", true);
    } finally {
      setBusy(false);
    }
  }

  async function pollPrintStatus(jobId, startedMs) {
    const maxWaitMs = 25 * 60 * 1000;
    while (Date.now() - startedMs < maxWaitMs) {
      const data = await api(`/api/print-labels/${encodeURIComponent(jobId)}/status`);
      if (data.message) {
        busyText.textContent = data.message;
      }
      if (data.status === "done") {
        return data;
      }
      if (data.status === "error") {
        throw new Error(data.error || data.message || "Print failed");
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    throw new Error("Print timed out — try limit 5–10 on cloud hosting");
  }

  async function printLabels() {
    const qs = storeQueryParams({
      limit: limitSelect.value,
      status: "ready_to_ship",
    });
    const selected = getSelection().length;
    setBusy(true, `Starting print (${selected} store${selected === 1 ? "" : "s"}, limit ${limitSelect.value} each)…`);
    const started = Date.now();
    try {
      const startedJob = await api(`/api/print-labels?${qs}`, { method: "POST" });
      const jobId = startedJob.job_id;
      if (!jobId) throw new Error("Print job did not return a job_id");
      currentJobId = jobId;
      const data = await pollPrintStatus(jobId, started);
      const secs = Math.round((Date.now() - started) / 1000);
      const downloadUrl =
        data.download_url || `/api/print-labels/${encodeURIComponent(jobId)}/download`;
      btnDownload.classList.remove("hidden");
      btnDownload.href = downloadUrl;
      btnDownload.textContent = "Download PDF";
      btnDownload.onclick = async (event) => {
        event.preventDefault();
        try {
          const token = await MultiStoreAuth.getAccessToken();
          const wid = MultiStoreAuth.getWorkspaceId();
          const res = await fetch(downloadUrl, {
            headers: {
              Authorization: `Bearer ${token}`,
              ...(wid ? { "X-Workspace-Id": wid } : {}),
            },
          });
          if (!res.ok) throw new Error("Download failed");
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          window.open(url, "_blank", "noopener");
        } catch (err) {
          showToast(err.message || "Download failed", true);
        }
      };
      renderLabelSources(data);
      showToast(`PDF ready · ${data.pages} page(s) · ${secs}s`);
      btnDownload.click();
    } catch (err) {
      showToast(err.message || "Print failed", true);
    } finally {
      setBusy(false);
    }
  }

  async function saveProfile() {
    if (!profileName || !profileSelect) {
      showToast("Could not save profile. Refresh the page and try again.", true);
      return;
    }
    const name = profileName.value.trim();
    if (!name) {
      showToast("Enter a profile name (e.g. Vendor Ali)", true);
      return;
    }
    const ids = getSelection();
    if (!ids.length) {
      showToast("Select at least one store first", true);
      return;
    }
    const data = await api("/api/store-groups", {
      method: "POST",
      body: JSON.stringify({ name, store_ids: ids }),
    });
    await loadServerGroups();
    if (data.group?.id) profileSelect.value = data.group.id;
    profileName.value = name;
    const state = loadVendorState();
    state.selection = ids;
    saveVendorState(state);
    showToast(`Saved profile “${name}” (${ids.length} store${ids.length === 1 ? "" : "s"})`);
  }

  async function loadProfile() {
    if (!profileSelect) return;
    const groupId = profileSelect.value;
    if (!groupId) return;
    const group = serverGroups.find((g) => g.id === groupId);
    if (!group?.store_ids?.length) return;
    setSelection(group.store_ids);
    profileName.value = group.name;
    showToast(`Loaded profile “${group.name}”`);
  }

  async function deleteProfile() {
    if (!profileSelect || !profileName) return;
    const groupId = profileSelect.value;
    if (!groupId) {
      showToast("Pick a profile to delete", true);
      return;
    }
    const group = serverGroups.find((g) => g.id === groupId);
    await api(`/api/store-groups/${encodeURIComponent(groupId)}`, { method: "DELETE" });
    profileSelect.value = "";
    profileName.value = "";
    await loadServerGroups();
    showToast(`Deleted profile “${group?.name || groupId}”`);
  }

  async function connectStore() {
    setBusy(true, "Opening Daraz authorization…");
    try {
      const data = await api("/api/oauth/start");
      if (!data.authorize_url) throw new Error("No authorize URL returned");
      location.href = data.authorize_url;
    } catch (err) {
      showToast(err.message || "Could not start OAuth", true);
      setBusy(false);
    }
  }

  btnSelectAll?.addEventListener("click", () => {
    if (profileSelect) profileSelect.value = "";
    setSelection(allStores.map((s) => s.store_id).filter(Boolean));
  });

  btnSelectNone?.addEventListener("click", () => {
    if (profileSelect) profileSelect.value = "";
    setSelection([]);
  });

  profileSelect?.addEventListener("change", () => {
    loadProfile().catch((err) => showToast(err.message || "Load failed", true));
  });
  btnSaveProfile?.addEventListener("click", () => {
    saveProfile().catch((err) => showToast(err.message || "Save failed", true));
  });
  btnDeleteProfile?.addEventListener("click", () => {
    deleteProfile().catch((err) => showToast(err.message || "Delete failed", true));
  });

  form.addEventListener("submit", loadOrders);
  btnRefresh.addEventListener("click", refreshTokens);
  btnPrint.addEventListener("click", printLabels);
  btnConnect?.addEventListener("click", connectStore);
  btnLogout?.addEventListener("click", async () => {
    await MultiStoreAuth.signOut();
    location.replace("/login");
  });

  async function boot() {
    const ok = await MultiStoreAuth.ready();
    if (!ok) {
      location.replace("/login");
      return;
    }
    const session = await MultiStoreAuth.getSession();
    if (!session) {
      location.replace("/login");
      return;
    }

    const me = await api("/api/me");
    if (me.workspace?.id) MultiStoreAuth.setWorkspaceId(me.workspace.id);
    if (userMeta) {
      userMeta.textContent = me.user?.email || "Signed in";
    }

    const params = new URLSearchParams(window.location.search);
    if (params.get("connected") === "1") {
      showToast("Store connected successfully");
      history.replaceState({}, "", "/");
    }

    await loadStores();
    await loadServerGroups();
    await maybeImportBrowserProfiles().catch(() => {});

    if (getSelection().length > 0) {
      limitSelect.value = "3";
      await loadOrders();
    }
  }

  boot().catch((err) => {
    showToast(err.message || "Could not start dashboard", true);
    setTimeout(() => location.replace("/login"), 1500);
  });
})();
