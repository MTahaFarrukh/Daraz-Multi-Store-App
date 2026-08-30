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
  let toastTimer = null;

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
    const res = await fetch(url, options);
    let data = null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      data = await res.json();
    } else {
      data = { detail: await res.text() };
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
      headers: { "Content-Type": "application/json" },
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

  function renderProfileOptions() {
    if (!profileSelect) return;
    const state = loadVendorState();
    const current = profileSelect.value;
    profileSelect.innerHTML = '<option value="">— Custom selection —</option>';
    for (const name of Object.keys(state.profiles).sort()) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      profileSelect.appendChild(opt);
    }
    if (current && state.profiles[current]) {
      profileSelect.value = current;
    }
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
      const label = document.createElement("label");
      label.className = "store-chip";
      const days =
        s.access_token_expires_in_seconds != null
          ? Math.max(0, Math.round(s.access_token_expires_in_seconds / 86400))
          : "?";
      const checked = initialSelection.includes(sid);
      const name = storeLabel(s);
      const showEmail = s.account && s.account !== name;
      if (checked) label.classList.add("selected");
      label.innerHTML = `
        <input type="checkbox" data-store-id="${escapeHtml(sid)}" ${checked ? "checked" : ""} />
        <span class="store-chip-body">
          <span class="store-chip-title">
            <strong>${escapeHtml(name)}</strong>
            <button type="button" class="btn-rename" title="Rename store" aria-label="Rename store">✎</button>
          </span>
          ${showEmail ? `<span class="store-email">${escapeHtml(s.account)}</span>` : ""}
          <span>${escapeHtml(s.country || "pk").toUpperCase()} · token ~${days}d left</span>
        </span>`;
      const input = label.querySelector("input");
      label.querySelector(".btn-rename")?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        renameStore(sid, name).catch((err) => showToast(err.message || "Rename failed", true));
      });
      input?.addEventListener("change", () => {
        label.classList.toggle("selected", input.checked);
        if (profileSelect) profileSelect.value = "";
        updateSelectionMeta();
        const next = loadVendorState();
        next.selection = getSelection();
        saveVendorState(next);
      });
      storeList.appendChild(label);
    }

    renderProfileOptions();
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

  function sourcePillClass(kind) {
    return kind === "converted" ? "source-pill converted" : "source-pill pdf";
  }

  function renderLabelSources(data) {
    if (!labelSourcesPanel || !labelSourcesBody) return;
    const details = data.label_details || [];
    const summary = data.label_summary || {};
    if (!details.length) {
      labelSourcesPanel.classList.add("hidden");
      return;
    }

    labelSourcesPanel.classList.remove("hidden");
    const pdfNative = summary.pdf_native ?? details.filter((d) => !d.converted).length;
    const htmlConverted = summary.html_converted ?? details.filter((d) => d.converted).length;
    labelSourcesMeta.textContent = `${pdfNative} native PDF · ${htmlConverted} HTML converted`;

    labelSourcesBody.innerHTML = "";
    for (const row of details) {
      const tr = document.createElement("tr");
      const finalText = row.converted ? "PDF (converted)" : "PDF (unchanged)";
      const finalKind = row.converted ? "converted" : "pdf";
      const noteParts = [];
      if (row.package_id) noteParts.push(`pkg ${row.package_id}`);
      if (row.fetch_notes) noteParts.push(row.fetch_notes);
      const noteText = noteParts.length ? noteParts.join(" · ") : "—";
      tr.innerHTML = `
        <td>${escapeHtml(row.store_name || "—")}</td>
        <td>${escapeHtml(String(row.order_id ?? "—"))}</td>
        <td><span class="${sourcePillClass(row.kind)}">${escapeHtml(row.display || "—")}</span></td>
        <td><span class="${sourcePillClass(finalKind)}">${escapeHtml(finalText)}</span></td>
        <td class="notes-cell">${escapeHtml(noteText)}</td>`;
      labelSourcesBody.appendChild(tr);
    }
  }

  async function loadStores() {
    const data = await api("/api/stores");
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

  async function pollPrintStatus(startedMs) {
    const maxWaitMs = 25 * 60 * 1000;
    while (Date.now() - startedMs < maxWaitMs) {
      const data = await api("/api/print-labels/status");
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
      await api(`/api/print-labels?${qs}`, { method: "POST" });
      const data = await pollPrintStatus(started);
      const secs = Math.round((Date.now() - started) / 1000);
      btnDownload.classList.remove("hidden");
      btnDownload.href = data.download_url || "/api/download/combined-labels";
      btnDownload.textContent = "Download PDF";
      renderLabelSources(data);
      const summary = data.label_summary || {};
      const native = summary.pdf_native ?? 0;
      const converted = summary.html_converted ?? 0;
      showToast(
        `PDF ready · ${data.pages} page(s) · ${native} Daraz PDF · ${converted} converted · ${secs}s`
      );
      window.open(btnDownload.href, "_blank", "noopener");
    } catch (err) {
      showToast(err.message || "Print failed", true);
    } finally {
      setBusy(false);
    }
  }

  function saveProfile() {
    if (!profileName || !profileSelect) {
      showToast("Profiles UI not available — hard refresh the page", true);
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
    const state = loadVendorState();
    state.profiles[name] = ids;
    state.selection = ids;
    saveVendorState(state);
    profileSelect.value = name;
    profileName.value = name;
    renderProfileOptions();
    profileSelect.value = name;
    showToast(`Saved profile “${name}” (${ids.length} store${ids.length === 1 ? "" : "s"})`);
  }

  function loadProfile() {
    if (!profileSelect) return;
    const name = profileSelect.value;
    if (!name) return;
    const state = loadVendorState();
    const ids = state.profiles[name];
    if (!ids?.length) return;
    setSelection(ids);
    profileName.value = name;
    showToast(`Loaded profile “${name}”`);
  }

  function deleteProfile() {
    if (!profileSelect || !profileName) return;
    const name = profileSelect.value || profileName.value.trim();
    if (!name) {
      showToast("Pick a profile to delete", true);
      return;
    }
    const state = loadVendorState();
    if (!state.profiles[name]) {
      showToast("Profile not found", true);
      return;
    }
    delete state.profiles[name];
    saveVendorState(state);
    profileSelect.value = "";
    profileName.value = "";
    renderProfileOptions();
    showToast(`Deleted profile “${name}”`);
  }

  btnSelectAll?.addEventListener("click", () => {
    if (profileSelect) profileSelect.value = "";
    setSelection(allStores.map((s) => s.store_id).filter(Boolean));
  });

  btnSelectNone?.addEventListener("click", () => {
    if (profileSelect) profileSelect.value = "";
    setSelection([]);
  });

  profileSelect?.addEventListener("change", loadProfile);
  btnSaveProfile?.addEventListener("click", saveProfile);
  btnDeleteProfile?.addEventListener("click", deleteProfile);

  form.addEventListener("submit", loadOrders);
  btnRefresh.addEventListener("click", refreshTokens);
  btnPrint.addEventListener("click", printLabels);

  const params = new URLSearchParams(window.location.search);
  if (params.get("connected") === "1") {
    showToast("Store connected successfully");
    history.replaceState({}, "", "/");
  }

  loadStores()
    .then(() => {
      if (getSelection().length > 0) {
        limitSelect.value = "3";
        return loadOrders();
      }
    })
    .catch((err) => showToast(err.message || "Could not load stores", true));
})();
