(() => {
  const storeList = document.getElementById("store-list");
  const storeCount = document.getElementById("store-count");
  const storeSelect = document.getElementById("store-select");
  const limitSelect = document.getElementById("limit-select");
  const ordersBody = document.getElementById("orders-body");
  const ordersMeta = document.getElementById("orders-meta");
  const btnRefresh = document.getElementById("btn-refresh");
  const btnPrint = document.getElementById("btn-print");
  const btnDownload = document.getElementById("btn-download");
  const form = document.getElementById("controls-form");
  const toast = document.getElementById("toast");
  const busy = document.getElementById("busy");
  const busyText = document.getElementById("busy-text");

  let toastTimer = null;

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
    busyText.textContent = text;
    busy.classList.toggle("hidden", !on);
    busy.setAttribute("aria-hidden", on ? "false" : "true");
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

  function renderStores(stores) {
    storeList.innerHTML = "";
    storeSelect.innerHTML = '<option value="">All stores</option>';

    if (!stores.length) {
      storeCount.textContent = "0 connected";
      storeList.innerHTML =
        '<div class="store-chip empty">No store yet — click Connect store</div>';
      btnRefresh.disabled = true;
      return;
    }

    storeCount.textContent = `${stores.length} connected`;
    btnRefresh.disabled = false;

    for (const s of stores) {
      const chip = document.createElement("div");
      chip.className = "store-chip";
      const days =
        s.access_token_expires_in_seconds != null
          ? Math.max(0, Math.round(s.access_token_expires_in_seconds / 86400))
          : "?";
      chip.innerHTML = `<strong>${escapeHtml(s.store_name || s.account || s.store_id)}</strong>
        <span>${escapeHtml(s.country || "pk").toUpperCase()} · token ~${days}d left</span>`;
      storeList.appendChild(chip);

      const opt = document.createElement("option");
      opt.value = s.store_id || "";
      opt.textContent = s.store_name || s.account || s.store_id;
      storeSelect.appendChild(opt);
    }
  }

  function renderOrders(orders) {
    ordersBody.innerHTML = "";
    if (!orders.length) {
      ordersMeta.textContent = "0 orders";
      ordersBody.innerHTML =
        '<tr class="empty-row"><td colspan="5">No ready-to-ship orders for this filter.</td></tr>';
      return;
    }
    ordersMeta.textContent = `${orders.length} order${orders.length === 1 ? "" : "s"}`;
    for (const o of orders) {
      const tr = document.createElement("tr");
      const statuses = Array.isArray(o.statuses) ? o.statuses.join(", ") : o.statuses || "—";
      tr.innerHTML = `
        <td>${escapeHtml(o.store_name || o.store_id || "—")}</td>
        <td>${escapeHtml(String(o.order_id ?? "—"))}</td>
        <td>${escapeHtml(String(o.items_count ?? "—"))}</td>
        <td><span class="status-pill">${escapeHtml(statuses)}</span></td>
        <td>${escapeHtml(o.created_at || "—")}</td>`;
      ordersBody.appendChild(tr);
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function loadStores() {
    const data = await api("/api/stores");
    renderStores(data.stores || []);
  }

  async function loadOrders(event) {
    if (event) event.preventDefault();
    const store = storeSelect.value;
    const limit = limitSelect.value;
    const qs = new URLSearchParams({ limit, status: "ready_to_ship" });
    if (store) qs.set("store", store);
    setBusy(true, "Loading orders…");
    try {
      const data = await api(`/api/orders?${qs}`);
      renderOrders(data.orders || []);
      showToast(`Loaded ${data.count || 0} orders`);
    } catch (err) {
      showToast(err.message || "Failed to load orders", true);
    } finally {
      setBusy(false);
    }
  }

  async function refreshTokens() {
    const store = storeSelect.value;
    const qs = new URLSearchParams({ force: "true" });
    if (store) qs.set("store", store);
    setBusy(true, "Refreshing tokens…");
    try {
      const data = await api(`/api/refresh-tokens?${qs}`, { method: "POST" });
      const bad = (data.results || []).filter((r) => r.status === "error");
      await loadStores();
      if (bad.length) {
        showToast(bad[0].error || "Refresh failed", true);
      } else {
        showToast("Tokens refreshed");
      }
    } catch (err) {
      showToast(err.message || "Refresh failed", true);
    } finally {
      setBusy(false);
    }
  }

  async function printLabels() {
    const store = storeSelect.value;
    const limit = limitSelect.value;
    const qs = new URLSearchParams({ limit, status: "ready_to_ship" });
    if (store) qs.set("store", store);
    setBusy(true, "Fetching labels & building PDF…");
    try {
      const data = await api(`/api/print-labels?${qs}`, { method: "POST" });
      btnDownload.classList.remove("hidden");
      showToast(`PDF ready · ${data.pages} page(s) from ${data.labels} label(s)`);
    } catch (err) {
      showToast(err.message || "Print failed", true);
    } finally {
      setBusy(false);
    }
  }

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
      if ((storeSelect.options.length || 0) > 1) {
        // auto-load a small batch for connected accounts
        limitSelect.value = "5";
        return loadOrders();
      }
    })
    .catch((err) => showToast(err.message || "Could not load stores", true));
})();
