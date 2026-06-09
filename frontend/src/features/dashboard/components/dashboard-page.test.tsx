import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";

import { renderWithProviders } from "@/test/utils";
import { createDashboardOverview, createDashboardProjections } from "@/test/mocks/factories";
import { useAccountMutations } from "@/features/accounts/hooks/use-accounts";
import { useDashboard, useDashboardProjections } from "@/features/dashboard/hooks/use-dashboard";
import { useRequestLogs } from "@/features/dashboard/hooks/use-request-logs";
import { buildDashboardView } from "@/features/dashboard/utils";

import { DashboardPage } from "./dashboard-page";

const { accountSummaryLineSpy } = vi.hoisted(() => ({
  accountSummaryLineSpy: vi.fn(),
}));

vi.mock("@/features/accounts/hooks/use-accounts", () => ({
  useAccountMutations: vi.fn(),
}));

vi.mock("@/features/dashboard/hooks/use-dashboard", () => ({
  useDashboard: vi.fn(),
  useDashboardProjections: vi.fn(),
}));

vi.mock("@/features/dashboard/hooks/use-request-logs", () => ({
  useRequestLogs: vi.fn(),
}));

vi.mock("@/features/dashboard/utils", () => ({
  buildDashboardView: vi.fn(),
}));

vi.mock("@/features/dashboard/components/account-cards", () => ({
  AccountCards: () => <div data-testid="account-cards" />,
}));

vi.mock("@/features/dashboard/components/account-summary-line", () => ({
  AccountSummaryLine: ({ accounts }: { accounts: Array<{ accountId: string }> }) => {
    accountSummaryLineSpy(accounts);
    return <div data-testid="account-summary-line">Summary for {accounts.length} accounts</div>;
  },
}));

vi.mock("@/features/dashboard/components/dashboard-skeleton", () => ({
  DashboardSkeleton: () => <div data-testid="dashboard-skeleton" />,
}));

vi.mock("@/features/dashboard/components/filters/overview-timeframe-select", () => ({
  OverviewTimeframeSelect: () => <div data-testid="overview-timeframe-select" />,
}));

vi.mock("@/features/dashboard/components/filters/request-filters", () => ({
  RequestFilters: () => <div data-testid="request-filters" />,
}));

vi.mock("@/features/dashboard/components/recent-requests-table", () => ({
  RecentRequestsTable: () => <div data-testid="recent-requests-table" />,
}));

vi.mock("@/features/dashboard/components/stats-grid", () => ({
  StatsGrid: () => <div data-testid="stats-grid" />,
}));

vi.mock("@/features/dashboard/components/usage-donuts", () => ({
  UsageDonuts: () => <div data-testid="usage-donuts" />,
}));

vi.mock("@/features/dashboard/components/weekly-credits-pace-card", () => ({
  WeeklyCreditsPaceCard: () => <div data-testid="weekly-credits-pace-card" />,
}));

const useAccountMutationsMock = vi.mocked(useAccountMutations);
const useDashboardMock = vi.mocked(useDashboard);
const useDashboardProjectionsMock = vi.mocked(useDashboardProjections);
const useRequestLogsMock = vi.mocked(useRequestLogs);
const buildDashboardViewMock = vi.mocked(buildDashboardView);

describe("DashboardPage", () => {
  beforeEach(() => {
    accountSummaryLineSpy.mockReset();
    useAccountMutationsMock.mockReset();
    useDashboardMock.mockReset();
    useDashboardProjectionsMock.mockReset();
    useRequestLogsMock.mockReset();
    buildDashboardViewMock.mockReset();
  });

  it("renders the account summary line in the Accounts header using overview accounts", () => {
    const overview = createDashboardOverview();

    useAccountMutationsMock.mockReturnValue({
      resumeMutation: { mutateAsync: vi.fn() },
      limitWarmupMutation: { mutateAsync: vi.fn() },
    } as unknown as ReturnType<typeof useAccountMutations>);
    useDashboardMock.mockReturnValue({
      data: overview,
      isFetching: false,
      error: null,
    } as ReturnType<typeof useDashboard>);
    useDashboardProjectionsMock.mockReturnValue({
      data: createDashboardProjections(),
      isFetching: false,
      error: null,
    } as ReturnType<typeof useDashboardProjections>);
    useRequestLogsMock.mockReturnValue({
      filters: {
        search: "",
        timeframe: "all",
        accountIds: [],
        apiKeyIds: [],
        modelOptions: [],
        statuses: [],
        limit: 25,
        offset: 0,
      },
      listFilters: {
        search: undefined,
        limit: 25,
        offset: 0,
        accountIds: [],
        apiKeyIds: [],
        statuses: [],
        modelOptions: [],
        since: undefined,
      },
      facetFilters: {
        since: undefined,
        accountIds: [],
        apiKeyIds: [],
        modelOptions: [],
      },
      logsQuery: {
        data: { requests: [], total: 0, hasMore: false },
        isFetching: false,
        error: null,
      },
      optionsQuery: {
        data: { accountIds: [], apiKeys: [], modelOptions: [], statuses: [] },
        error: null,
      },
      updateFilters: vi.fn(),
    } as unknown as ReturnType<typeof useRequestLogs>);
    buildDashboardViewMock.mockReturnValue({
      stats: [],
      weeklyCreditPace: null,
      primaryUsageItems: [],
      secondaryUsageItems: [],
      primaryTotal: 0,
      secondaryTotal: 0,
      safeLinePrimary: null,
      safeLineSecondary: null,
      requestLogs: [],
    } as ReturnType<typeof buildDashboardView>);

    renderWithProviders(<DashboardPage />);

    const accountsHeader = screen.getByRole("heading", { name: "Accounts" }).parentElement;

    expect(accountsHeader).not.toBeNull();
    expect(within(accountsHeader as HTMLElement).getByTestId("account-summary-line")).toHaveTextContent(
      "Summary for 2 accounts",
    );
    expect(accountSummaryLineSpy).toHaveBeenCalledWith(overview.accounts);
  });
});
