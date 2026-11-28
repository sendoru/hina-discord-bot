(() => {
  const bulkForm = document.querySelector("[data-runtime-reset-all-form]");
  const bulkButton = bulkForm?.querySelector('button[type="submit"]');
  if (!bulkForm || !bulkButton || !window.fetch || !window.DOMParser) return;

  const rows = () => Array.from(document.querySelectorAll("[data-runtime-config-key]"));
  const rowFor = (key) => document.getElementById(`runtime-setting-${key}`);
  const collectionFor = (key) => document.getElementById(`runtime-collection-${key}`);
  const statusFor = (key) => key === "all"
    ? document.querySelector("[data-runtime-bulk-status]")
    : rowFor(key)?.querySelector("[data-runtime-command-status]");
  const routeKeys = ["provider", "model", "fast_model", "smart_model", "output_repair_model"];
  const pending = new Map();
  let bulkDisabled = bulkButton.disabled;
  let refreshTail = Promise.resolve();

  const message = (key, text, error = false, retry = null) => {
    const status = statusFor(key);
    if (!status) return;
    status.replaceChildren(document.createTextNode(text));
    status.hidden = !text;
    status.classList.toggle("field-error", error);
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = "Check again";
      button.addEventListener("click", retry, { once: true });
      status.append(" ", button);
    }
  };

  const lock = (key, locked) => {
    const row = rowFor(key);
    if (!row) return;
    if (locked) row.setAttribute("aria-busy", "true");
    else row.removeAttribute("aria-busy");
    for (const cell of row.querySelectorAll(".runtime-config-current, .runtime-config-action, .runtime-config-reset")) {
      cell.inert = locked;
    }
    const collection = collectionFor(key);
    if (collection) collection.inert = locked;
  };

  const updateBulk = () => {
    bulkButton.disabled = bulkDisabled || pending.size > 0;
  };

  const request = async (url, options = {}) => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 8000);
    try {
      return await fetch(url, { ...options, cache: "no-store", signal: controller.signal });
    } finally {
      window.clearTimeout(timer);
    }
  };
  const delay = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  const refresh = (key, replaceSettings) => {
    // Serialize reads as well as DOM updates: a slow older snapshot must not win.
    const work = refreshTail.catch(() => {}).then(async () => {
      const response = await request("/admin/runtime", { headers: { Accept: "text/html" } });
      if (!response.ok) throw new Error(`Refresh failed (${response.status})`);
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextBulk = page.querySelector('[data-runtime-reset-all-form] button[type="submit"]');
      const nextActions = page.querySelector("[data-runtime-recent-actions]");
      const actions = document.querySelector("[data-runtime-recent-actions]");
      if (!nextBulk || !nextActions || !actions) throw new Error("Invalid settings snapshot");
      const keys = replaceSettings ? (key === "all" ? rows().map((row) => row.dataset.runtimeConfigKey) : key === "provider" ? routeKeys : [key]) : [];
      const replacements = [];
      for (const setting of keys) {
        for (const id of [`runtime-setting-${setting}`, `runtime-config-help-${setting}`, `runtime-collection-${setting}`]) {
          const old = document.getElementById(id);
          const next = page.getElementById(id);
          if (!old && !next) continue;
          if (!old || !next) throw new Error("Incomplete settings snapshot");
          replacements.push([old, next]);
        }
      }
      const scrollX = window.scrollX;
      const scrollY = window.scrollY;
      for (const [old, next] of replacements) {
        if (old.hasAttribute("data-runtime-config-key")) {
          next.hidden = old.hidden;
          const oldHelp = old.querySelector("[data-runtime-config-help-toggle]");
          const newHelp = next.querySelector("[data-runtime-config-help-toggle]");
          if (oldHelp && newHelp) newHelp.setAttribute("aria-expanded", oldHelp.getAttribute("aria-expanded") || "false");
        } else if (old.hasAttribute("data-runtime-config-help-row")) {
          next.hidden = old.hidden;
        }
        old.replaceWith(next);
      }
      actions.replaceWith(nextActions);
      bulkButton.textContent = nextBulk.textContent;
      bulkDisabled = nextBulk.disabled;
      updateBulk();
      document.dispatchEvent(new Event("runtime-config:refresh"));
      window.scrollTo(scrollX, scrollY);
    });
    refreshTail = work;
    return work;
  };

  const finish = (job, text, error = false) => {
    pending.delete(job.key);
    if (job.key === "all") rows().forEach((row) => lock(row.dataset.runtimeConfigKey, false));
    else if (job.key === "provider") routeKeys.forEach((key) => lock(key, false));
    else lock(job.key, false);
    // A completed or rejected request must not reuse the command nonce on a later save.
    const nonce = job.form.elements.namedItem("_request_id");
    if (nonce) nonce.value = Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) => n.toString(16).padStart(2, "0")).join("");
    updateBulk();
    message(job.key, text, error);
  };

  const run = async (job) => {
    if (job.running) return;
    job.running = true;
    try {
      if (!job.id) {
        message(job.key, "Submitting…");
        const response = await request(job.form.action, {
          method: "POST", headers: { Accept: "application/json" }, body: job.body,
        });
        if (response.status >= 400 && response.status < 500) {
          const detail = await response.json().catch(() => ({}));
          finish(job, String(detail.detail || `Request rejected (${response.status})`), true);
          return;
        }
        if (!response.ok) throw new Error(`Submission failed (${response.status})`);
        const result = await response.json();
        if (!Number.isSafeInteger(result.command_id) || result.command_id <= 0) throw new Error("Invalid command ID");
        job.id = result.command_id;
      }
      message(job.key, `Applying #${job.id}…`);
      const deadline = Date.now() + 20_000;
      while (!job.result && Date.now() < deadline) {
        try {
          const response = await request(`/admin/commands/${encodeURIComponent(job.id)}`, { headers: { Accept: "application/json" } });
          if (response.ok) {
            const result = await response.json();
            if (result.status === "succeeded" || result.status === "failed") job.result = result;
          }
        } catch (_) { /* Retry transient read errors. */ }
        if (!job.result) await delay(350);
      }
      if (!job.result) throw new Error("Command is still pending or its status is unavailable");
      const success = job.result.status === "succeeded";
      await refresh(job.key, success);
      const detail = job.result.error_message || job.result.error_type || "Unknown error";
      finish(job, success ? `Applied #${job.id}` : `Failed #${job.id}: ${detail}`, !success);
    } catch (error) {
      // Keep the original request body and ID: retry is idempotent even if the POST response was lost.
      message(job.key, `${error.message || "Request interrupted"}. Check again to reconcile the result.`, true, () => void run(job));
    } finally {
      job.running = false;
    }
  };

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement)) return;
    const path = new URL(form.action, window.location.href).pathname;
    if (!["/admin/runtime/set", "/admin/runtime/reset", "/admin/runtime/reset-all", "/admin/runtime/provider"].includes(path)) return;
    event.preventDefault();
    const body = new URLSearchParams(new FormData(form));
    const key = path.endsWith("/reset-all") ? "all" : path.endsWith("/provider") ? "provider" : body.get("key");
    if (!key || (key !== "all" && !rowFor(key)) || pending.has(key) || pending.has("all")) return;
    if (key === "all" && pending.size) return;
    if (key === "provider" && routeKeys.some((setting) => pending.has(setting))) return;
    if (routeKeys.includes(key) && pending.has("provider")) return;
    const job = { key, form, body, id: null, result: null, running: false };
    pending.set(key, job);
    if (key === "all") rows().forEach((row) => lock(row.dataset.runtimeConfigKey, true));
    else if (key === "provider") routeKeys.forEach((setting) => lock(setting, true));
    else lock(key, true);
    updateBulk();
    void run(job);
  });
})();
