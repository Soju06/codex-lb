import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";

function setScrollY(value: number): void {
  Object.defineProperty(window, "scrollY", {
    configurable: true,
    value,
  });
}

describe("route scroll flow integration", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    setScrollY(0);
  });

  it("starts pathname-changing top-level navigation at the top", async () => {
    const user = userEvent.setup({ delay: null });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {
      setScrollY(0);
    });

    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    setScrollY(1400);
    scrollTo.mockClear();

    await user.click(screen.getAllByRole("link", { name: "Dashboard" })[0]);

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    await waitFor(() => expect(window.scrollY).toBe(0));
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, left: 0, behavior: "auto" });
  });
});
