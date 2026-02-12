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
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1_200);
    });
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  });

  it("shows error toast when clipboard write fails", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("clipboard blocked"));
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
});
