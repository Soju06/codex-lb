import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/features/accounts/components/accounts-page", () => {
  return {
    AccountsPage() {
      throw new Error("Rejected route chunk");
    },
  };
});

describe("route recovery flow integration", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.history.pushState({}, "", "/");
  });

  it("keeps the shell and supports keyboard recovery for an unknown route", async () => {
    const user = userEvent.setup({ delay: null });
    window.history.pushState({}, "", "/definitely-unknown");

    renderWithProviders(<App />);
    const recovery = await screen.findByTestId("route-not-found");

    expect(screen.getByRole("banner")).toBeVisible();
    expect(screen.getByRole("main")).toContainElement(recovery);
    expect(screen.getByRole("contentinfo")).toBeVisible();
    expect(within(recovery).getByTestId("route-recovery-heading")).toHaveFocus();

    const dashboardLink = within(recovery).getByTestId("route-dashboard-link");
    await user.tab();
    expect(dashboardLink).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(window.location.pathname).toBe("/dashboard"));
    expect(screen.queryByTestId("route-not-found")).not.toBeInTheDocument();
  });

  it("contains a rejected lazy route and exposes keyboard recovery", async () => {
    const user = userEvent.setup({ delay: null });
    window.history.pushState({}, "", "/accounts");

    renderWithProviders(<App />);
    const recovery = await screen.findByTestId("route-load-error");

    expect(recovery).toHaveAttribute("role", "alert");
    expect(screen.getByRole("banner")).toBeVisible();
    expect(screen.getByRole("main")).toContainElement(recovery);
    expect(screen.getByRole("contentinfo")).toBeVisible();
    expect(within(recovery).getByTestId("route-recovery-heading")).toHaveFocus();

    const retry = within(recovery).getByTestId("route-retry");
    const dashboardLink = within(recovery).getByTestId("route-dashboard-link");
    expect(retry).toBeEnabled();
    expect(dashboardLink).toHaveAttribute("href", "/dashboard");
    await user.tab();
    expect(retry).toHaveFocus();
    await user.click(dashboardLink);

    await waitFor(() => expect(window.location.pathname).toBe("/dashboard"));
    expect(screen.queryByTestId("route-load-error")).not.toBeInTheDocument();
  });
});
