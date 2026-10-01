(() => {
  function syncScopePicker(picker) {
    const selected = picker.querySelector('input[type="radio"]:checked');
    const type = selected ? selected.value : "";
    const guildInput = picker.querySelector('[data-scope-field="guild"] input');
    const channelField = picker.querySelector('[data-scope-field="channel"]');
    const channelInput = channelField ? channelField.querySelector("input") : null;
    const dmChannel = picker.dataset.dmChannel !== "false";

    if (guildInput) {
      guildInput.disabled = type !== "guild";
    }
    if (channelInput) {
      channelInput.disabled = type !== "guild" && !(type === "dm" && dmChannel);
    }
    if (channelField) {
      channelField.hidden = type === "dm" && !dmChannel;
    }
  }

  function setupScopePicker(picker) {
    syncScopePicker(picker);
    picker.addEventListener("change", (event) => {
      if (event.target.matches('input[type="radio"]')) {
        syncScopePicker(picker);
      }
    });
  }

  document.querySelectorAll(".scope-picker").forEach(setupScopePicker);
})();
