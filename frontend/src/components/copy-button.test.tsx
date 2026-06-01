import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CopyButton } from "@/components/copy-button";

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
  },
}));

describe("CopyButton", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("writes to clipboard and shows success feedback", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<CopyButton value="secret-value" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledWith("secret-value");
    expect(toastSuccess).toHaveBeenCalledWith("Copied to clipboard");
    expect(screen.getByRole("button", { name: "Copy Copied" })).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1_200);
    });
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  });

  it("shows error toast when clipboard write fails", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("clipboard blocked"));
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<CopyButton value="secret-value" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
      await Promise.resolve();
    });

    expect(toastError).toHaveBeenCalledWith("Failed to copy");
  });

  it("supports icon-only copy buttons with accessible labeling", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<CopyButton value="secret-value" label="Copy Request ID" iconOnly />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy Request ID" }));
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledWith("secret-value");
    expect(screen.getByRole("button", { name: "Copy Request ID Copied" })).toBeInTheDocument();
  });

  it("keeps keyboard focus on the trigger after copying", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<CopyButton value="secret-value" />);
    const copyButton = screen.getByRole("button", { name: "Copy" });

    copyButton.focus();
    expect(copyButton).toHaveFocus();

    await act(async () => {
      fireEvent.click(copyButton);
      await Promise.resolve();
    });

    expect(copyButton).toHaveFocus();
  });

  it("restores trigger focus after fallback copy inside a dialog", async () => {
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: false,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });

    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);

    let fallbackActiveTag: string | undefined;
    let fallbackParent: Element | null = null;
    const execCommand = vi.fn(() => {
      const active = document.activeElement as HTMLTextAreaElement | null;
      fallbackActiveTag = active?.tagName;
      fallbackParent = active?.parentElement ?? null;
      return true;
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    render(<CopyButton value="secret-value" />, { container: dialog });

    const copyButton = screen.getByRole("button", { name: "Copy" });
    copyButton.focus();

    await act(async () => {
      fireEvent.click(copyButton);
      await Promise.resolve();
    });

    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(fallbackActiveTag).toBe("TEXTAREA");
    expect(fallbackParent).toBe(dialog);
    expect(copyButton).toHaveFocus();
    dialog.remove();
  });
});
