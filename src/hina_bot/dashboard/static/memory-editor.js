(() => {
  const form = document.querySelector("[data-memory-edit-form]");
  if (form instanceof HTMLFormElement) {
    const kind = form.querySelector("[data-memory-kind]");
    const evidence = form.querySelector("[data-relationship-evidence]");
    const syncEvidence = () => {
      if (!(kind instanceof HTMLSelectElement) || !(evidence instanceof HTMLElement)) return;
      const active = kind.value === "relationship";
      const writeEnabled = form.dataset.writeEnabled === "true";
      evidence.hidden = !active;
      evidence.querySelectorAll("input").forEach((input) => {
        if (!(input instanceof HTMLInputElement)) return;
        input.disabled = !active || !writeEnabled;
      });
    };
    syncEvidence();
    if (kind instanceof HTMLSelectElement) {
      kind.addEventListener("change", syncEvidence);
    }
  }

  document.addEventListener("submit", (event) => {
    const submitted = event.target;
    if (!(submitted instanceof HTMLFormElement)) return;
    const submitter = event.submitter;
    if (!(submitter instanceof HTMLElement)) return;
    const target = submitter.dataset.confirmRetract;
    if (!target) return;

    if (!window.confirm(
      `정말 이 memory를 retract할까요?\n\n${target}\n\n내용과 provenance는 보존되지만 runtime memory에서는 제외됩니다.`,
    )) {
      event.preventDefault();
      return;
    }

    let confirmation = submitted.querySelector('input[name="confirm"]');
    if (!(confirmation instanceof HTMLInputElement)) {
      confirmation = document.createElement("input");
      confirmation.type = "hidden";
      confirmation.name = "confirm";
      submitted.appendChild(confirmation);
    }
    confirmation.value = "yes";
  });

  const notice = document.querySelector("[data-admin-command-watch]");
  if (!(notice instanceof HTMLElement)) return;
  const commandId = notice.dataset.adminCommandWatch;
  if (!commandId) return;

  const clearQueuedParamAndReload = () => {
    const url = new URL(window.location.href);
    url.searchParams.delete("queued");
    window.location.replace(url.toString());
  };
  const failNotice = (message) => {
    notice.classList.add("warning");
    notice.textContent = message;
  };
  const poll = async () => {
    const deadline = Date.now() + 20_000;
    while (Date.now() < deadline) {
      try {
        const response = await fetch(
          `/admin/commands/${encodeURIComponent(commandId)}`,
          { headers: { Accept: "application/json" }, cache: "no-store" },
        );
        if (response.ok) {
          const command = await response.json();
          if (command.status === "succeeded") {
            clearQueuedParamAndReload();
            return;
          }
          if (command.status === "failed") {
            failNotice(
              `Admin command #${commandId} 적용에 실패했습니다: ${command.error_message || command.error_type || "알 수 없는 오류"}`,
            );
            return;
          }
        }
      } catch (_) {
        // Retry transient dashboard read failures until the short watch window expires.
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    notice.textContent =
      `Admin command #${commandId}가 아직 처리 중입니다. 잠시 후 새로고침하면 상태를 확인할 수 있습니다.`;
  };
  void poll();
})();
