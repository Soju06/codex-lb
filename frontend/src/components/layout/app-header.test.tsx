import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppHeader } from "@/components/layout/app-header";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { server } from "@/test/mocks/server";
import {
  ADMIN_PERMISSIONS,
  OPERATOR_PERMISSIONS,
  createAccountSummary,
  createDashboardSettings,
  createSessionUser,
} from "@/test/mocks/factories";

function renderHeader(initialEntry = "/dashboard", onLogout = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <AppHeader onLogout={onLogout} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppHeader", () => {
  beforeEach(() => {
    useAuthStore.setState({
      permissions: ADMIN_PERMISSIONS,
      user: null,
      tier: "individual",
      canWrite: true,
      passwordManagementEnabled: true,
      passwordSessionActive: false,
    });
  });

  it("shows the summed Accounts reset-credit badge capped at 99+", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({ availableResetCredits: 70 }),
            createAccountSummary({ accountId: "acc-2", availableResetCredits: 40 }),
          ],
        }),
      ),
    );

    renderHeader();

    expect(await screen.findAllByText("99+")).not.toHaveLength(0);
  });

  it("sums reset-credit badge across accounts and treats missing counts as zero", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({ availableResetCredits: 5 }),
            createAccountSummary({ accountId: "acc-2" }),
            createAccountSummary({ accountId: "acc-3", availableResetCredits: null }),
            createAccountSummary({ accountId: "acc-4", availableResetCredits: 3 }),
          ],
        }),
      ),
    );

    renderHeader();

    expect(await screen.findAllByText("8")).not.toHaveLength(0);
  });

  it("hides the Accounts reset-credit badge when no resets are available", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({ availableResetCredits: 0 }),
            createAccountSummary({ accountId: "acc-2", availableResetCredits: 0 }),
          ],
        }),
      ),
    );

    renderHeader();

    await screen.findByRole("link", { name: /Accounts/i });
    expect(screen.queryByText("99+")).not.toBeInTheDocument();
  });

  it("hides the Accounts reset-credit badge when settings disable reset-credit badges", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [createAccountSummary({ availableResetCredits: 5 })],
        }),
      ),
      http.get("/api/settings", () =>
        HttpResponse.json(createDashboardSettings({ showResetCreditBadges: false })),
      ),
    );

    renderHeader();

    await screen.findByRole("link", { name: /Accounts/i });
    await waitFor(() => {
      expect(screen.queryByText("5")).not.toBeInTheDocument();
    });
  });

  it("renders core destinations as top-level links and keeps Automations out of the pill bar", async () => {
    renderHeader();

    expect(await screen.findByRole("link", { name: /Dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Reports/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Accounts/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /APIs/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Settings/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Automations" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Advanced" })).toBeInTheDocument();
  });

  it("reveals Automations after opening the Advanced menu", async () => {
    const user = userEvent.setup();
    renderHeader();

    await user.click(screen.getByRole("button", { name: "Advanced" }));

    expect(await screen.findByRole("menuitem", { name: "Automations" })).toBeInTheDocument();
  });

  it("marks the Advanced trigger active only while an advanced route is current", () => {
    renderHeader("/automations");
    expect(screen.getByRole("button", { name: "Advanced" })).toHaveAttribute("data-active", "true");
  });

  it("keeps the Advanced trigger inactive on core routes", () => {
    renderHeader("/dashboard");
    expect(screen.getByRole("button", { name: "Advanced" })).toHaveAttribute("data-active", "false");
  });

  describe("account tiering", () => {
    it("keeps today's Logout button on the individual tier even for a signed-in account", async () => {
      const user = userEvent.setup();
      const onLogout = vi.fn();
      useAuthStore.setState({ user: createSessionUser(), tier: "individual" });

      renderHeader("/dashboard", onLogout);

      const logout = screen.getByRole("button", { name: "Logout" });
      expect(screen.queryByRole("button", { name: /admin\s*·\s*Admin/ })).not.toBeInTheDocument();
      await user.click(logout);
      expect(onLogout).toHaveBeenCalledTimes(1);
    });

    it("never shows the account chip to an implicit admin without an account", () => {
      useAuthStore.setState({ user: null, tier: "team" });

      renderHeader();

      expect(screen.getByRole("button", { name: "Logout" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /admin\s*·\s*Admin/ })).not.toBeInTheDocument();
    });

    it("shows the avatar chip named by its visible text and the full menu on the team tier", async () => {
      const user = userEvent.setup();
      useAuthStore.setState({
        user: createSessionUser(),
        tier: "team",
        canWrite: true,
        passwordManagementEnabled: true,
        passwordSessionActive: true,
      });

      renderHeader();

      expect(screen.queryByRole("button", { name: "Logout" })).not.toBeInTheDocument();
      const chip = screen.getByRole("button", { name: /admin\s*·\s*Admin/ });

      await user.click(chip);
      expect(await screen.findByRole("menuitem", { name: "My password" })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: "My two-factor" })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: "Log out everywhere" })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: "Logout" })).toBeInTheDocument();
      expect(screen.queryByRole("menuitem", { name: /Invite/ })).not.toBeInTheDocument();
    });

    it("offers My two-factor only when the Settings page would show the TOTP card", async () => {
      const user = userEvent.setup();
      useAuthStore.setState({
        permissions: OPERATOR_PERMISSIONS,
        user: createSessionUser({ username: "ops", role: { id: "r2", slug: "operator", name: "Operator", kind: "preset" } }),
        tier: "team",
        canWrite: true,
        passwordManagementEnabled: true,
        passwordSessionActive: false,
      });

      renderHeader();

      await user.click(screen.getByRole("button", { name: /ops\s*·\s*Operator/ }));
      expect(await screen.findByRole("menuitem", { name: "My password" })).toBeInTheDocument();
      expect(screen.queryByRole("menuitem", { name: "My two-factor" })).not.toBeInTheDocument();
    });

    it("is keyboard operable: Enter opens the menu and the arrow keys reach the items", async () => {
      const user = userEvent.setup();
      useAuthStore.setState({ user: createSessionUser(), tier: "team" });

      renderHeader();

      screen.getByRole("button", { name: /admin\s*·\s*Admin/ }).focus();
      await user.keyboard("{Enter}");
      const first = await screen.findByRole("menuitem", { name: "My password" });
      await waitFor(() => expect(first).toHaveFocus());
      await user.keyboard("{ArrowDown}");
      expect(screen.getByRole("menuitem", { name: "Log out everywhere" })).toHaveFocus();
    });

    it("opens the change-password dialog from the menu", async () => {
      const user = userEvent.setup();
      useAuthStore.setState({ user: createSessionUser(), tier: "team" });

      renderHeader();

      await user.click(screen.getByRole("button", { name: /admin\s*·\s*Admin/ }));
      await user.click(await screen.findByRole("menuitem", { name: "My password" }));

      expect(await screen.findByRole("heading", { name: "Change password" })).toBeInTheDocument();
    });

    it("calls the logout-all endpoint from Log out everywhere", async () => {
      const user = userEvent.setup();
      const logoutEverywhere = vi.fn().mockResolvedValue(undefined);
      const logout = vi.fn().mockResolvedValue(undefined);
      useAuthStore.setState({ user: createSessionUser(), tier: "team", logoutEverywhere, logout });

      renderHeader();

      await user.click(screen.getByRole("button", { name: /admin\s*·\s*Admin/ }));
      await user.click(await screen.findByRole("menuitem", { name: "Log out everywhere" }));

      expect(logoutEverywhere).toHaveBeenCalledTimes(1);
      expect(logout).not.toHaveBeenCalled();
    });
  });

  describe("permission-aware navigation", () => {
    it("hides nav items whose permission the session lacks", () => {
      useAuthStore.setState({ permissions: ["read", "dashboard:read:all"] });

      renderHeader();

      expect(screen.getByRole("link", { name: /Dashboard/i })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Settings/i })).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: /Accounts/i })).not.toBeInTheDocument();
    });

    it("keeps every core item for the guest grant set", () => {
      useAuthStore.setState({ permissions: ["read", "accounts:read:all", "dashboard:read:all"] });

      renderHeader();

      for (const name of [/Dashboard/i, /Reports/i, /Accounts/i, /APIs/i, /Settings/i]) {
        expect(screen.getByRole("link", { name })).toBeInTheDocument();
      }
    });
  });
});
