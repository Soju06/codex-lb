type CopyToClipboardOptions = {
  container?: HTMLElement | null;
};

function fallbackCopyToClipboard(
  text: string,
  options: CopyToClipboardOptions,
): boolean {
  if (typeof document.execCommand !== "function") {
    return false;
  }

  const container = options.container ?? document.body;
  if (!container) {
    return false;
  }

  const previousActiveElement =
    document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.setAttribute("aria-hidden", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "-9999px";
  textarea.style.opacity = "0";

  container.appendChild(textarea);
  textarea.focus();
  textarea.select();

  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    if (textarea.parentNode === container) {
      container.removeChild(textarea);
    }
    if (previousActiveElement?.isConnected) {
      previousActiveElement.focus();
    }
  }
}

export async function copyToClipboard(
  text: string,
  options: CopyToClipboardOptions = {},
): Promise<boolean> {
  const clipboardWritePromise =
    window.isSecureContext && typeof navigator.clipboard?.writeText === "function"
      ? navigator.clipboard.writeText(text).then(() => true).catch(() => false)
      : null;

  if (fallbackCopyToClipboard(text, options)) {
    if (clipboardWritePromise) {
      void clipboardWritePromise;
    }
    return true;
  }

  return clipboardWritePromise ?? false;
}
