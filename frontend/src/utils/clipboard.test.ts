import { afterEach, describe, expect, it, vi } from "vitest";

import { copyToClipboard } from "@/utils/clipboard";

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
const originalIsSecureContext = Object.getOwnPropertyDescriptor(window, "isSecureContext");
const originalExecCommand = Object.getOwnPropertyDescriptor(document, "execCommand");

describe("copyToClipboard", () => {
  afterEach(() => {
    if (originalClipboard) {
      Object.defineProperty(navigator, "clipboard", originalClipboard);
    }

    if (originalIsSecureContext) {
      Object.defineProperty(window, "isSecureContext", originalIsSecureContext);
    }

    if (originalExecCommand) {
      Object.defineProperty(document, "execCommand", originalExecCommand);
    }

    vi.restoreAllMocks();
  });

  it("uses navigator clipboard in secure contexts", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    await expect(copyToClipboard("hello")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
  });

  it("keeps the execCommand fallback synchronous when clipboard write is blocked", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("clipboard blocked"));
    const execCommand = vi.fn(() => {
      expect(document.activeElement?.tagName).toBe("TEXTAREA");
      return true;
    });
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    await expect(copyToClipboard("hello")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("falls back to execCommand when Clipboard API is unavailable", async () => {
    const focusTarget = document.createElement("button");
    document.body.appendChild(focusTarget);
    focusTarget.focus();

    const execCommand = vi.fn(() => {
      expect(document.activeElement?.tagName).toBe("TEXTAREA");
      return true;
    });
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: false,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    await expect(copyToClipboard("hello")).resolves.toBe(true);
    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(focusTarget).toHaveFocus();
    expect(document.querySelectorAll("textarea")).toHaveLength(0);

    focusTarget.remove();
  });

  it("returns false when execCommand throws", async () => {
    const execCommand = vi.fn(() => {
      throw new Error("copy failed");
    });
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: false,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    await expect(copyToClipboard("hello")).resolves.toBe(false);
  });

  it("uses provided container as fallback target", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const focusTarget = document.createElement("button");
    container.appendChild(focusTarget);
    focusTarget.focus();

    const execCommand = vi.fn(() => {
      const active = document.activeElement as HTMLTextAreaElement | null;
      expect(active?.tagName).toBe("TEXTAREA");
      expect(active?.parentElement).toBe(container);
      return true;
    });

    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: false,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    await expect(copyToClipboard("hello", { container })).resolves.toBe(true);
    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(focusTarget).toHaveFocus();

    container.remove();
  });
});
