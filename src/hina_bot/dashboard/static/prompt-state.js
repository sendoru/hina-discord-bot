(() => {
  const REMOVE_FIELD = "confirm";

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;

    const submitter = event.submitter;
    if (!(submitter instanceof HTMLElement)) return;

    const target = submitter.dataset.confirmRemove;
    if (!target) return;

    if (!window.confirm(`정말 삭제할까요?\n\n${target}`)) {
      event.preventDefault();
      return;
    }

    let confirmation = form.querySelector(`input[name="${REMOVE_FIELD}"]`);
    if (!(confirmation instanceof HTMLInputElement)) {
      confirmation = document.createElement("input");
      confirmation.type = "hidden";
      confirmation.name = REMOVE_FIELD;
      form.appendChild(confirmation);
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
          {
            headers: { Accept: "application/json" },
            cache: "no-store",
          },
        );
        if (response.ok) {
          const command = await response.json();
          if (command.status === "succeeded") {
            clearQueuedParamAndReload();
            return;
          }
          if (command.status === "failed") {
            const detail =
              command.error_message ||
              command.error_type ||
              "알 수 없는 오류";
            failNotice(`Admin command #${commandId} 적용에 실패했습니다: ${detail}`);
            return;
          }
        }
      } catch (_) {
        // A transient dashboard read failure should not turn a successful write into
        // a visible error. Retry until the short watch window expires.
      }

      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }

    notice.textContent =
      `Admin command #${commandId}가 아직 처리 중입니다. 잠시 후 새로고침하면 상태를 확인할 수 있습니다.`;
  };

  void poll();
})();
