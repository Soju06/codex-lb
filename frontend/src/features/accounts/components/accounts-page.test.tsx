import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { AccountsPage } from "@/features/accounts/components/accounts-page";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { ADMIN_PERMISSIONS, createUpstreamProxyAdmin } from "@/test/mocks/factories";
import type { AccountSummary } from "@/features/accounts/schemas";

vi.mock("@/features/accounts/hooks/use-accounts", () => ({
  useAccounts: vi.fn(),
  useAccountTrends: vi.fn(() => ({ data: null })),
  useAccountUsageResetCredits: vi.fn(() => ({
    data: { rateLimitResetCredits: { availableCount: 3 } },
    isFetching: false,
    error: null,
  })),
}));

vi.mock("@/features/accounts/hooks/use-oauth", () => ({
  useOauth: vi.fn(() => ({
    state: {
      status: "idle",
      method: null,
      authorizationUrl: null,
      callbackUrl: null,
      verificationUrl: null,
      userCode: null,
      deviceAuthId: null,
      intervalSeconds: null,
      expiresInSeconds: null,
      errorMessage: null,
    },
    start: vi.fn(),
    complete: vi.fn(),
    manualCallback: vi.fn(),
    reset: vi.fn(),
  })),
}));

vi.mock("@/features/settings/hooks/use-settings", () => ({
  useSettings: vi.fn(() => ({
    settingsQuery: {
      data: {
        showResetCreditBadges: true,
        showResetCreditExpiryBadge: true,
      },
      error: null,
    },
  })),
  useUpstreamProxyAdmin: vi.fn(() => ({
    upstreamProxyQuery: { data: null, error: null },
    accountBindingMutation: {
      isPending: false,
      error: null,
      mutateAsync: vi.fn(),
    },
    testEndpointMutation: {
      isPending: false,
      error: null,
      mutateAsync: vi.fn(),
    },
  })),
}));

const { useAccounts } = await import("@/features/accounts/hooks/use-accounts");
const mockedUseAccounts = useAccounts as unknown as ReturnType<typeof vi.fn>;
const { useUpstreamProxyAdmin } = await import("@/features/settings/hooks/use-settings");
const mockedUseUpstreamProxyAdmin = useUpstreamProxyAdmin as unknown as ReturnType<typeof vi.fn>;

function mockAccountsQuery(accounts: AccountSummary[]) {
  mockedUseAccounts.mockReturnValue({
    accountsQuery: { data: accounts, error: null, refetch: vi.fn() },
    importMutation: idleMutation(),
    pauseMutation: idleMutation(),
    resumeMutation: idleMutation(),
    probeMutation: idleMutation(),
    usageResetMutation: idleMutation(),
    deleteMutation: idleMutation(),
    exportAuthMutation: idleMutation(),
    setAliasMutation: idleMutation(),
    limitWarmupMutation: idleMutation(),
    usageLimitMutation: idleMutation(),
    routingPolicyMutation: idleMutation(),
    updateMutation: idleMutation(),
  } as unknown as ReturnType<typeof useAccounts>);
}

function idleMutation() {
  return {
    isPending: false,
    error: null,
    mutateAsync: vi.fn(),
  };
}

function account(overrides: Partial<AccountSummary>): AccountSummary {
  return {
    accountId: "acc-default",
    email: "default@example.com",
    displayName: "Default",
    planType: "plus",
    status: "active",
    usageLimitEnabled: false,
    usageLimitPercent: null,
    usageLimitState: "disabled",
    additionalQuotas: [],
    limitWarmupEnabled: false,
    ...overrides,
  };
}

describe("AccountsPage", () => {
  beforeEach(() => {
    // The auth store starts least-privilege; these cases exercise admin actions.
    useAuthStore.setState({
      initialized: true,
      authenticated: true,
      role: "admin",
      permissions: ADMIN_PERMISSIONS,
      canWrite: true,
    });
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "weekly" });
    vi.spyOn(Date, "now").mockReturnValue(
      new Date("2026-01-01T12:00:00.000Z").getTime(),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useAuthStore.setState({ role: "admin", permissions: ADMIN_PERMISSIONS, canWrite: true });
  });

  it("keeps the upstream-proxy admin query idle and hides OAuth help for read-only guests", () => {
    useAuthStore.setState({ role: "guest", permissions: ["read"], canWrite: false, initialized: true });
    // Guests receive a masked identity: no ChatGPT account/workspace ids and a redacted email.
    mockAccountsQuery([
      account({
        accountId: "acc-masked",
        email: "m***@example.com",
        displayName: "m***@example.com",
        chatgptAccountId: null,
        workspaceId: null,
      }),
    ]);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(mockedUseUpstreamProxyAdmin).toHaveBeenCalledWith({ enabled: false });
    expect(screen.queryByRole("button", { name: "Need help?" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add account" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "m***@example.com" })).toBeInTheDocument();
    expect(screen.getAllByText(/Personal \/ unknown workspace/).length).toBeGreaterThan(0);
  });

  it("does not render cached upstream-proxy data in the proxy-binding panel for read-only guests", () => {
    useAuthStore.setState({ role: "guest", permissions: ["read"], canWrite: false, initialized: true });
    // `enabled: false` only stops fetching; an earlier admin session's response
    // can still be in the cache and must not reach the panel.
    mockedUseUpstreamProxyAdmin.mockReturnValue({
      upstreamProxyQuery: { data: createUpstreamProxyAdmin(), error: null },
      accountBindingMutation: { isPending: false, error: null, mutateAsync: vi.fn() },
      testEndpointMutation: { isPending: false, error: null, mutateAsync: vi.fn() },
    });
    mockAccountsQuery([account({ accountId: "acc_primary" })]);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "Accounts" })).toBeInTheDocument();
    expect(screen.queryByText("Proxy binding")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Account proxy pool" })).not.toBeInTheDocument();
    expect(screen.queryByText("Primary pool")).not.toBeInTheDocument();
  });

  it("renders the proxy-binding panel from upstream-proxy data for writers", () => {
    mockedUseUpstreamProxyAdmin.mockReturnValue({
      upstreamProxyQuery: { data: createUpstreamProxyAdmin(), error: null },
      accountBindingMutation: { isPending: false, error: null, mutateAsync: vi.fn() },
      testEndpointMutation: { isPending: false, error: null, mutateAsync: vi.fn() },
    });
    mockAccountsQuery([account({ accountId: "acc_primary" })]);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Proxy binding")).toBeInTheDocument();
  });

  it("enables the upstream-proxy admin query and shows OAuth help for writers", () => {
    mockAccountsQuery([account({})]);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(mockedUseUpstreamProxyAdmin).toHaveBeenCalledWith({ enabled: true });
    expect(screen.getByRole("button", { name: "Need help?" })).toBeInTheDocument();
  });

  it("defaults the selected account to the first account after display sorting", () => {
    mockedUseAccounts.mockReturnValue({
      accountsQuery: {
        data: [
          account({
            accountId: "acc-api-first",
            email: "api-first@example.com",
            displayName: "API First",
            resetAtSecondary: "2026-01-01T13:00:00.000Z",
            windowMinutesSecondary: 10_080,
          }),
          account({
            accountId: "acc-visible-first",
            email: "visible-first@example.com",
            displayName: "Visible First",
            resetAtSecondary: "2026-01-01T12:10:00.000Z",
            windowMinutesSecondary: 10_080,
          }),
        ],
        error: null,
        refetch: vi.fn(),
      },
      importMutation: idleMutation(),
      pauseMutation: idleMutation(),
      resumeMutation: idleMutation(),
      probeMutation: idleMutation(),
      usageResetMutation: idleMutation(),
      deleteMutation: idleMutation(),
      exportAuthMutation: idleMutation(),
      setAliasMutation: idleMutation(),
      limitWarmupMutation: idleMutation(),
      usageLimitMutation: idleMutation(),
      routingPolicyMutation: idleMutation(),
      updateMutation: idleMutation(),
    } as unknown as ReturnType<typeof useAccounts>);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(
      screen
        .getAllByText(/^(Visible First|API First)$/)
        .map((el) => el.textContent),
    ).toEqual(["Visible First", "API First", "Visible First"]);
    expect(
      screen.getByRole("heading", { name: "Visible First" }),
    ).toBeInTheDocument();
  });

  it("renders account panels with mobile-first responsive containment", () => {
    mockedUseAccounts.mockReturnValue({
      accountsQuery: {
        data: [
          account({
            accountId: "acc-long",
            email: "very.long.account.identity.for.mobile@example-enterprise-workspace.invalid",
            displayName: "very.long.account.identity.for.mobile@example-enterprise-workspace.invalid",
          }),
        ],
        error: null,
        refetch: vi.fn(),
      },
      importMutation: idleMutation(),
      pauseMutation: idleMutation(),
      resumeMutation: idleMutation(),
      probeMutation: idleMutation(),
      usageResetMutation: idleMutation(),
      deleteMutation: idleMutation(),
      exportAuthMutation: idleMutation(),
      setAliasMutation: idleMutation(),
      limitWarmupMutation: idleMutation(),
      usageLimitMutation: idleMutation(),
      routingPolicyMutation: idleMutation(),
      updateMutation: idleMutation(),
    } as unknown as ReturnType<typeof useAccounts>);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("accounts-layout")).toHaveClass(
      "grid-cols-1",
      "min-w-0",
      "lg:grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)]",
    );
    expect(screen.getByTestId("accounts-list-panel")).toHaveClass(
      "min-w-0",
      "min-h-0",
      "self-start",
    );
    expect(screen.getByTestId("accounts-list-panel")).not.toHaveClass("h-full");
    expect(screen.getByTestId("accounts-list-card")).not.toHaveClass("h-full");
    expect(screen.getByRole("heading", { name: /very\.long\.account/i })).toHaveClass(
      "min-w-0",
      "truncate",
    );
  });

  it("keeps helper instructions accessible when no accounts exist", async () => {
    const user = userEvent.setup();

    mockedUseAccounts.mockReturnValue({
      accountsQuery: {
        data: [],
        error: null,
        refetch: vi.fn(),
      },
      importMutation: idleMutation(),
      pauseMutation: idleMutation(),
      resumeMutation: idleMutation(),
      probeMutation: idleMutation(),
      usageResetMutation: idleMutation(),
      deleteMutation: idleMutation(),
      exportAuthMutation: idleMutation(),
      setAliasMutation: idleMutation(),
      limitWarmupMutation: idleMutation(),
      usageLimitMutation: idleMutation(),
      routingPolicyMutation: idleMutation(),
      updateMutation: idleMutation(),
    } as unknown as ReturnType<typeof useAccounts>);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "Need help?" }));
    expect(screen.getByText("Windows OAuth Help")).toBeInTheDocument();
  });

  it("confirms before resetting selected account usage", async () => {
    const user = userEvent.setup();
    const resetUsage = vi.fn().mockResolvedValue({
      status: "reset",
      accountId: "acc-reset",
      code: "reset",
      windowsReset: 2,
      usageWritten: true,
      primaryUsedPercentBefore: 100,
      primaryUsedPercentAfter: 2,
      secondaryUsedPercentBefore: 80,
      secondaryUsedPercentAfter: 0,
      accountStatusBefore: "rate_limited",
      accountStatusAfter: "active",
    });

    mockedUseAccounts.mockReturnValue({
      accountsQuery: {
        data: [
          account({
            accountId: "acc-reset",
            email: "reset@example.com",
            displayName: "Resettable",
            resetAtSecondary: "2026-01-01T13:00:00.000Z",
            windowMinutesSecondary: 10_080,
          }),
        ],
        error: null,
        refetch: vi.fn(),
      },
      importMutation: idleMutation(),
      pauseMutation: idleMutation(),
      resumeMutation: idleMutation(),
      probeMutation: idleMutation(),
      usageResetMutation: {
        isPending: false,
        error: null,
        mutateAsync: resetUsage,
      },
      deleteMutation: idleMutation(),
      exportAuthMutation: idleMutation(),
      setAliasMutation: idleMutation(),
      limitWarmupMutation: idleMutation(),
      usageLimitMutation: idleMutation(),
      routingPolicyMutation: idleMutation(),
      updateMutation: idleMutation(),
    } as unknown as ReturnType<typeof useAccounts>);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "Reset usage" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Reset usage" });
    expect(resetUsage).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Reset" }));

    await waitFor(() => {
      expect(resetUsage).toHaveBeenCalledWith({ accountId: "acc-reset" });
    });
  });

  it("keeps force probe as an immediate action", async () => {
    const user = userEvent.setup();
    const probe = vi.fn().mockResolvedValue({
      status: "probed",
      accountId: "acc-probe",
      probeStatusCode: 200,
      primaryUsedPercentBefore: 10,
      primaryUsedPercentAfter: 9,
      secondaryUsedPercentBefore: 20,
      secondaryUsedPercentAfter: 19,
      accountStatusBefore: "active",
      accountStatusAfter: "active",
    });

    mockedUseAccounts.mockReturnValue({
      accountsQuery: {
        data: [
          account({
            accountId: "acc-probe",
            email: "probe@example.com",
            displayName: "Probe account",
            resetAtSecondary: "2026-01-01T13:00:00.000Z",
            windowMinutesSecondary: 10_080,
          }),
        ],
        error: null,
        refetch: vi.fn(),
      },
      importMutation: idleMutation(),
      pauseMutation: idleMutation(),
      resumeMutation: idleMutation(),
      probeMutation: {
        isPending: false,
        error: null,
        mutateAsync: probe,
      },
      usageResetMutation: idleMutation(),
      deleteMutation: idleMutation(),
      exportAuthMutation: idleMutation(),
      setAliasMutation: idleMutation(),
      limitWarmupMutation: idleMutation(),
      usageLimitMutation: idleMutation(),
      routingPolicyMutation: idleMutation(),
      updateMutation: idleMutation(),
    } as unknown as ReturnType<typeof useAccounts>);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "Force probe" }));

    expect(probe).toHaveBeenCalledWith({ accountId: "acc-probe" });
    expect(screen.queryByRole("alertdialog", { name: "Reset usage" })).not.toBeInTheDocument();
  });
});
