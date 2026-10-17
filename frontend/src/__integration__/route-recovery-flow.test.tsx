import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import {
  RouteErrorBoundary,
  RouteLoadError,
} from "@/components/layout/route-recovery";
import { renderWithProviders } from "@/test/utils";

const dashboardMockState = vi.hoisted(() => ({ failed: false }));
const sameUrlBoundaryState = { failed: false };

function SameUrlBoundaryHarness() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <>
      <button onClick={() => void navigate(location.pathname)} type="button">
        Navigate same URL
      </button>
      <RouteErrorBoundary resetKey={location.key}>
        {sameUrlBoundaryState.failed ? (
          <SameUrlFailure />
        ) : (
          <div data-testid="same-url-route-loaded">Same URL route recovered</div>
        )}
      </RouteErrorBoundary>
    </>
  );
}

function SameUrlFailure(): never {
  throw new Error("Same URL route failed");
}

vi.mock("@/features/dashboard/components/dashboard-page", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/features/dashboard/components/dashboard-page")>();
  const DashboardPage = actual.DashboardPage;

  return {
    ...actual,
    DashboardPage() {
      if (dashboardMockState.failed) {
        throw new Error("Dashboard route render failed");
      }
      return <DashboardPage />;
    },
  };
});

vi.mock("@/features/accounts/components/accounts-page", () => {
  throw new Error("Rejected route chunk");
});

vi.mock("@/features/settings/components/settings-page", () => {
  return {
    SettingsPage() {
      const { hash, search } = useLocation();
      if (search || hash) {
        throw new Error("Settings route render failed");
      }
      return <div data-testid="settings-route-loaded">Settings route recovered</div>;
    },
  };
});

describe("route recovery flow integration", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    dashboardMockState.failed = false;
    sameUrlBoundaryState.failed = false;
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

  it("remounts a failed route when search and hash recover on the same path", async () => {
    window.history.pushState({}, "", "/settings?advanced=1#firewall");

    renderWithProviders(<App />);
    expect(await screen.findByTestId("route-load-error")).toBeInTheDocument();

    window.history.pushState({}, "", "/settings");
    window.dispatchEvent(new PopStateEvent("popstate"));

    expect(await screen.findByTestId("settings-route-loaded")).toHaveTextContent(
      "Settings route recovered",
    );
    expect(screen.queryByTestId("route-load-error")).not.toBeInTheDocument();
  });

  it("preserves dashboard search input across healthy same-path URL updates", async () => {
    const user = userEvent.setup({ delay: null });
    window.history.pushState({}, "", "/dashboard");

    renderWithProviders(<App />);
    const search = await screen.findByRole("textbox");
    await user.click(search);
    await user.type(search, "abc");

    expect(search).toHaveValue("abc");
    expect(search).toHaveFocus();
    expect(window.location.search).toContain("search=abc");
    expect(screen.queryByTestId("route-load-error")).not.toBeInTheDocument();
  });

  it.each(["/dashboard", "/dashboard/", "/Dashboard", "/%64ashboard"])(
    "offers a document reload when the failed route is %s",
    async (pathname) => {
    dashboardMockState.failed = true;
    window.history.pushState({}, "", pathname);

    renderWithProviders(<App />);
    const recovery = await screen.findByTestId("route-load-error");
    const dashboardLink = within(recovery).getByRole("link", {
      name: "Go to Dashboard",
    });

    expect(dashboardLink).toHaveAttribute("href", "/dashboard");
    expect(dashboardLink).not.toHaveAttribute("data-discover");
    },
  );

  it.each(["/dashboard%2F", "/%64ashboard%2F"])(
    "does not treat encoded segment separators as Dashboard at %s",
    async (pathname) => {
      window.history.pushState({}, "", pathname);

      renderWithProviders(<RouteLoadError />);
      const recovery = await screen.findByTestId("route-load-error");
      const dashboardLink = within(recovery).getByRole("link", {
        name: "Go to Dashboard",
      });

      expect(dashboardLink).toHaveAttribute("data-discover", "true");
    },
  );

  it("resets the route boundary when navigation changes only location.key", async () => {
    const user = userEvent.setup({ delay: null });
    sameUrlBoundaryState.failed = true;
    window.history.pushState({}, "", "/same-url");

    renderWithProviders(<SameUrlBoundaryHarness />);
    expect(await screen.findByTestId("route-load-error")).toBeVisible();

    sameUrlBoundaryState.failed = false;
    await user.click(screen.getByRole("button", { name: "Navigate same URL" }));

    expect(await screen.findByTestId("same-url-route-loaded")).toBeVisible();
    expect(screen.queryByTestId("route-load-error")).not.toBeInTheDocument();
  });

  it("retains a new render failure introduced by a healthy location update", async () => {
    window.history.pushState({}, "", "/settings");

    renderWithProviders(<App />);
    expect(await screen.findByTestId("settings-route-loaded")).toBeVisible();

    window.history.pushState({}, "", "/settings?advanced=1#firewall");
    window.dispatchEvent(new PopStateEvent("popstate"));

    expect(await screen.findByTestId("route-load-error")).toBeVisible();
    expect(screen.queryByTestId("settings-route-loaded")).not.toBeInTheDocument();
  });
});
