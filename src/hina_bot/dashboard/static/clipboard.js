(() => {
  const copiedLabel = "Copied";
  let toastTimer;

  function ensureToast() {
    let toast = document.querySelector(".copy-toast");
    if (toast) return toast;
    toast = document.createElement("div");
    toast.className = "copy-toast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    document.body.appendChild(toast);
    return toast;
  }

  function showCopied() {
    const toast = ensureToast();
    toast.textContent = copiedLabel;
    toast.classList.add("visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 1200);
  }

  async function writeClipboard(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  async function copyText(text, trigger) {
    if (!text) return;
    try {
      await writeClipboard(text);
      trigger?.classList.add("is-copied");
      showCopied();
      window.setTimeout(() => trigger?.classList.remove("is-copied"), 1200);
    } catch {
      const toast = ensureToast();
      toast.textContent = "Copy failed";
      toast.classList.add("visible");
      window.clearTimeout(toastTimer);
      toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 1600);
    }
  }

  function makeBlockCopyable(source) {
    if (source.closest(".copy-block")) return;
    const wrapper = document.createElement("div");
    wrapper.className = "copy-block";
    source.parentNode.insertBefore(wrapper, source);
    wrapper.appendChild(source);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-button";
    button.setAttribute("aria-label", "Copy");
    button.title = "Copy";
    button.textContent = "⧉";
    button.addEventListener("click", () => copyText(source.textContent ?? "", button));
    wrapper.appendChild(button);
  }

  function prepareAtomicCopy(target) {
    if (!target.hasAttribute("tabindex")) target.tabIndex = 0;
    if (!target.hasAttribute("role")) target.setAttribute("role", "button");
    if (!target.hasAttribute("title")) target.title = "Click to copy";
    if (!target.hasAttribute("aria-label")) {
      target.setAttribute("aria-label", `Copy ${target.textContent?.trim() || "value"}`);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("pre:not([data-copy-disabled])").forEach(makeBlockCopyable);
    document.querySelectorAll("[data-copy-block]").forEach(makeBlockCopyable);
    document.querySelectorAll("[data-copy-text]").forEach(prepareAtomicCopy);
  });

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-copy-text]");
    if (!target || target.closest("a, button")) return;
    copyText(target.dataset.copyText || target.textContent || "", target);
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target.closest("[data-copy-text]");
    if (!target || target.closest("a, button")) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    copyText(target.dataset.copyText || target.textContent || "", target);
  });
})();
