(() => {
  const editors = document.querySelectorAll("[data-runtime-config-editor]");

  const closeEditor = (editor, { restore = true } = {}) => {
    const view = editor.querySelector("[data-runtime-config-view]");
    const form = editor.querySelector("[data-runtime-config-form]");
    const input = editor.querySelector("[data-runtime-config-input]");
    if (!view || !form || !input) return;
    if (restore) input.value = input.dataset.originalValue || "";
    form.hidden = true;
    view.hidden = false;
  };

  for (const editor of editors) {
    const view = editor.querySelector("[data-runtime-config-view]");
    const form = editor.querySelector("[data-runtime-config-form]");
    const input = editor.querySelector("[data-runtime-config-input]");
    const edit = editor.querySelector("[data-runtime-config-edit]");
    const cancel = editor.querySelector("[data-runtime-config-cancel]");
    if (!view || !form || !input || !edit || !cancel) continue;

    edit.addEventListener("click", () => {
      view.hidden = true;
      form.hidden = false;
      input.focus();
      input.select();
    });

    cancel.addEventListener("click", () => closeEditor(editor));

    input.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeEditor(editor);
      edit.focus();
    });
  }
})();
