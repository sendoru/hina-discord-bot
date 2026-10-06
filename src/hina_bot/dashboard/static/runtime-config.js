(() => {
  const scalarEditors = document.querySelectorAll("[data-runtime-config-editor]");

  const closeScalarEditor = (editor, { restore = true } = {}) => {
    const input = editor.querySelector("[data-runtime-config-input]");
    const actionView = editor.querySelector("[data-runtime-config-action-view]");
    const actionEdit = editor.querySelector("[data-runtime-config-action-edit]");
    if (!input || !actionView || !actionEdit) return;
    if (restore) input.value = input.dataset.originalValue || "";
    editor.classList.remove("is-editing");
    actionEdit.hidden = true;
    actionView.hidden = false;
  };

  for (const editor of scalarEditors) {
    const input = editor.querySelector("[data-runtime-config-input]");
    const edit = editor.querySelector("[data-runtime-config-edit]");
    const cancel = editor.querySelector("[data-runtime-config-cancel]");
    const actionView = editor.querySelector("[data-runtime-config-action-view]");
    const actionEdit = editor.querySelector("[data-runtime-config-action-edit]");
    if (!input || !edit || !cancel || !actionView || !actionEdit) continue;

    edit.addEventListener("click", () => {
      editor.classList.add("is-editing");
      actionView.hidden = true;
      actionEdit.hidden = false;
      input.focus();
      if (input instanceof HTMLInputElement && input.type === "text") {
        input.select();
      }
    });

    cancel.addEventListener("click", () => closeScalarEditor(editor));

    input.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeScalarEditor(editor);
      edit.focus();
    });
  }

  const tableWrap = document.querySelector(".runtime-config-table-wrap");
  const helpRows = Array.from(
    document.querySelectorAll("[data-runtime-config-help-row]"),
  );
  const helpButtons = Array.from(
    document.querySelectorAll("[data-runtime-config-help-toggle]"),
  );
  const collectionRows = Array.from(
    document.querySelectorAll("[data-runtime-collection-row]"),
  );
  const manageButtons = Array.from(
    document.querySelectorAll("[data-runtime-collection-manage]"),
  );

  const rowForKey = (key) =>
    collectionRows.find((row) => row.dataset.runtimeCollectionRow === key);

  const buttonForKey = (key) =>
    manageButtons.find((button) => button.dataset.runtimeCollectionManage === key);

  const availableExpandedRowWidth = () =>
    tableWrap instanceof HTMLElement ? Math.max(0, tableWrap.clientWidth - 24) : 0;

  const sizeHelpRow = (row) => {
    const help = row.querySelector(".runtime-config-help");
    if (!(help instanceof HTMLElement)) return;
    const available = availableExpandedRowWidth();
    if (available > 0) {
      help.style.setProperty("--runtime-config-help-width", `${available}px`);
    }
  };

  const sizeCollectionEditor = (row) => {
    const editor = row.querySelector("[data-runtime-collection-editor]");
    if (!(editor instanceof HTMLElement)) return;
    const available = availableExpandedRowWidth();
    editor.style.setProperty(
      "--runtime-collection-editor-width",
      `${available}px`,
    );
  };

  const hideCollectionRow = (row, { restore = true } = {}) => {
    const editor = row.querySelector("[data-runtime-collection-editor]");
    if (!(editor instanceof HTMLElement)) return;
    const resetDraft = editor._resetCollectionDraft;
    if (restore && typeof resetDraft === "function") resetDraft();
    row.hidden = true;
    const key = row.dataset.runtimeCollectionRow;
    const button = key ? buttonForKey(key) : null;
    if (button) button.setAttribute("aria-expanded", "false");
  };

  const closeOtherCollectionRows = (except) => {
    for (const row of collectionRows) {
      if (row !== except && !row.hidden) hideCollectionRow(row);
    }
  };

  const helpRowForKey = (key) =>
    helpRows.find((row) => row.dataset.runtimeConfigHelpRow === key);

  for (const button of helpButtons) {
    button.addEventListener("click", () => {
      const key = button.dataset.runtimeConfigHelpToggle;
      if (!key) return;
      const row = helpRowForKey(key);
      if (!(row instanceof HTMLTableRowElement)) return;

      const opening = row.hidden;
      row.hidden = !opening;
      button.setAttribute("aria-expanded", opening ? "true" : "false");
      if (opening) sizeHelpRow(row);
    });
  }

  const createEditableChip = (value) => {
    const chip = document.createElement("span");
    chip.className = "runtime-collection-chip runtime-collection-chip-editable";
    chip.dataset.runtimeCollectionItem = "";
    chip.dataset.value = value;

    const code = document.createElement("code");
    code.textContent = value;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "runtime-collection-chip-remove";
    remove.dataset.runtimeCollectionRemove = "";
    remove.setAttribute("aria-label", `Remove ${value}`);
    remove.textContent = "×";

    chip.append(code, remove);
    return chip;
  };

  for (const row of collectionRows) {
    const editor = row.querySelector("[data-runtime-collection-editor]");
    if (!(editor instanceof HTMLElement)) continue;

    const form = editor.matches("[data-runtime-collection-form]")
      ? editor
      : editor.querySelector("[data-runtime-collection-form]");
    const list = editor.querySelector("[data-runtime-collection-list]");
    const hiddenValue = editor.querySelector("[data-runtime-collection-value]");
    const addInput = editor.querySelector("[data-runtime-collection-add-input]");
    const addButton = editor.querySelector("[data-runtime-collection-add]");
    const clearButton = editor.querySelector("[data-runtime-collection-clear]");
    const cancelButton = editor.querySelector("[data-runtime-collection-cancel]");
    const saveButton = editor.querySelector("[data-runtime-collection-save]");
    const count = editor.querySelector("[data-runtime-collection-count]");
    const error = editor.querySelector("[data-runtime-collection-error]");

    if (
      !(form instanceof HTMLFormElement) ||
      !(list instanceof HTMLElement) ||
      !(hiddenValue instanceof HTMLInputElement) ||
      !(addInput instanceof HTMLInputElement) ||
      !(addButton instanceof HTMLButtonElement) ||
      !(cancelButton instanceof HTMLButtonElement) ||
      !(saveButton instanceof HTMLButtonElement) ||
      !(count instanceof HTMLElement) ||
      !(error instanceof HTMLElement)
    ) {
      continue;
    }

    const kind = editor.dataset.kind || "";
    const maxItems = Number.parseInt(editor.dataset.maxItems || "0", 10);
    const emptyAllowed = editor.dataset.emptyAllowed === "true";
    const initialItems = Array.from(
      list.querySelectorAll("[data-runtime-collection-item]"),
      (item) => item.dataset.value || "",
    );
    let draft = [...initialItems];

    const sameAsInitial = () =>
      draft.length === initialItems.length &&
      draft.every((value, index) => value === initialItems[index]);

    const showError = (message = "") => {
      error.textContent = message;
      error.hidden = !message;
    };

    const sync = () => {
      list.replaceChildren(...draft.map(createEditableChip));
      hiddenValue.value = draft.join(", ");
      count.textContent = `${draft.length} ${draft.length === 1 ? "item" : "items"}`;
      const invalidEmpty = !emptyAllowed && draft.length === 0;
      saveButton.disabled = sameAsInitial() || invalidEmpty;
      showError(invalidEmpty ? "이 설정은 최소 한 개의 값이 필요합니다." : "");
    };

    const normalizeNewItem = (raw) => {
      const value = raw.trim();
      if (kind === "discord_ids" && /^\d+$/.test(value)) {
        try {
          return BigInt(value).toString();
        } catch (_) {
          return value;
        }
      }
      return value;
    };

    const validateNewItem = (value) => {
      if (!value) return "값을 입력해 주세요.";
      if (draft.includes(value)) return "이미 목록에 있는 값입니다.";
      if (maxItems > 0 && draft.length >= maxItems) {
        return `최대 ${maxItems}개까지 추가할 수 있습니다.`;
      }

      if (kind === "prefixes") {
        if (value.includes(",")) return "접두어 하나에는 쉼표를 사용할 수 없습니다.";
        if (value.length > 32) return "접두어는 32자 이하여야 합니다.";
        if (/[\r\n\0]/.test(value)) return "접두어에는 줄바꿈이나 NUL을 사용할 수 없습니다.";
      } else if (kind === "discord_ids") {
        if (!/^\d+$/.test(value)) return "Discord ID는 양의 정수여야 합니다.";
        try {
          if (BigInt(value) <= 0n) return "Discord ID는 양의 정수여야 합니다.";
        } catch (_) {
          return "Discord ID 형식이 올바르지 않습니다.";
        }
      }
      return "";
    };

    const addItem = () => {
      const value = normalizeNewItem(addInput.value);
      const message = validateNewItem(value);
      if (message) {
        showError(message);
        addInput.focus();
        return;
      }
      draft.push(value);
      addInput.value = "";
      sync();
      addInput.focus();
    };

    editor._resetCollectionDraft = () => {
      draft = [...initialItems];
      addInput.value = "";
      sync();
    };

    addButton.addEventListener("click", addItem);

    addInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addItem();
      } else if (event.key === "Escape") {
        event.preventDefault();
        hideCollectionRow(row);
        const key = row.dataset.runtimeCollectionRow;
        const button = key ? buttonForKey(key) : null;
        if (button) button.focus();
      }
    });

    list.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const remove = target.closest("[data-runtime-collection-remove]");
      if (!(remove instanceof HTMLElement)) return;
      const chip = remove.closest("[data-runtime-collection-item]");
      if (!(chip instanceof HTMLElement)) return;
      const value = chip.dataset.value || "";
      const index = draft.indexOf(value);
      if (index < 0) return;
      draft.splice(index, 1);
      sync();
    });

    if (clearButton instanceof HTMLButtonElement) {
      clearButton.addEventListener("click", () => {
        draft = [];
        sync();
      });
    }

    cancelButton.addEventListener("click", () => {
      hideCollectionRow(row);
      const key = row.dataset.runtimeCollectionRow;
      const button = key ? buttonForKey(key) : null;
      if (button) button.focus();
    });

    form.addEventListener("submit", (event) => {
      if (!emptyAllowed && draft.length === 0) {
        event.preventDefault();
        showError("이 설정은 최소 한 개의 값이 필요합니다.");
        return;
      }
      hiddenValue.value = draft.join(", ");
      saveButton.disabled = true;
    });

    sync();
  }

  for (const button of manageButtons) {
    button.addEventListener("click", () => {
      const key = button.dataset.runtimeCollectionManage;
      if (!key) return;
      const row = rowForKey(key);
      if (!row) return;

      if (!row.hidden) {
        hideCollectionRow(row);
        return;
      }

      closeOtherCollectionRows(row);
      row.hidden = false;
      sizeCollectionEditor(row);
      button.setAttribute("aria-expanded", "true");
      const addInput = row.querySelector("[data-runtime-collection-add-input]");
      if (addInput instanceof HTMLInputElement) addInput.focus();
    });
  }

  if (tableWrap instanceof HTMLElement && "ResizeObserver" in window) {
    const observer = new ResizeObserver(() => {
      for (const row of helpRows) {
        if (!row.hidden) sizeHelpRow(row);
      }
      for (const row of collectionRows) {
        if (!row.hidden) sizeCollectionEditor(row);
      }
    });
    observer.observe(tableWrap);
  } else {
    window.addEventListener("resize", () => {
      for (const row of helpRows) {
        if (!row.hidden) sizeHelpRow(row);
      }
      for (const row of collectionRows) {
        if (!row.hidden) sizeCollectionEditor(row);
      }
    });
  }

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
        // Retry transient dashboard read failures during the short watch window.
      }

      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }

    notice.textContent =
      `Admin command #${commandId}가 아직 처리 중입니다. 잠시 후 새로고침하면 상태를 확인할 수 있습니다.`;
  };

  void poll();
})();
