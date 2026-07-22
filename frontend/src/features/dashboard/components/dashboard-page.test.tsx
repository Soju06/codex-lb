import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/utils";
import { createDashboardOverview, createDashboardProjections } from "@/test/mocks/factories";
import { useAccountMutations } from "@/features/accounts/hooks/use-accounts";
import { useDashboard, useDashboardProjections } from "@/features/dashboard/hooks/use-dashboard";
import { useRequestLogs } from "@/features/dashboard/hooks/use-request-logs";
import { buildDashboardView } from "@/features/dashboard/utils";
import { useDashboardPreferencesStore } from "@/hooks/use-dashboard-preferences";

import { DashboardPage } from "./dashboard-page";

const { accountCardsSpy, accountListSpy, accountSummaryLineSpy } = vi.hoisted(() => ({
  accountCardsSpy: vi.fn(),
  accountListSpy: vi.fn(),
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
  AccountCards: ({ accounts }: { accounts: Array<{ accountId: string }> }) => {
    accountCardsSpy(accounts);
    return <div data-testid="account-cards">Cards for {accounts.length} accounts</div>;
  },
}));

vi.mock("@/features/dashboard/components/account-list", () => ({
  AccountList: ({
    accounts,
    sort,
    onSortChange,
  }: {
    accounts: Array<{ accountId: string }>;
    sort: { key: string; direction: string } | null;
    onSortChange: (sort: { key: string; direction: string }) => void;
  }) => {
    accountListSpy({ accounts, sort });
    return (
      <button
        type="button"
        data-testid="account-list"
        onClick={() => onSortChange({ key: "credits", direction: "desc" })}
      >
        List for {accounts.length} accounts
      </button>
    );
  },
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

vi.mock("@/features/dashboard/components/filters/request-filters", async () => {
  const actual = await vi.importActual<typeof import("@/features/dashboard/components/filters/request-filters")>("@/features/dashboard/components/filters/request-filters");
  return actual;
});

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

type RequestLogsQueryOverrides = {
  data?: undefined;
  error?: Error | null;
  isFetching?: boolean;
  isLoading?: boolean;
  isPending?: boolean;
  isSuccess?: boolean;
};

describe("DashboardPage", () => {
  beforeEach(() => {
    accountCardsSpy.mockReset();
    accountListSpy.mockReset();
    accountSummaryLineSpy.mockReset();
    useAccountMutationsMock.mockReset();
    useDashboardMock.mockReset();
    useDashboardProjectionsMock.mockReset();
    useRequestLogsMock.mockReset();
    buildDashboardViewMock.mockReset();
    useDashboardPreferencesStore.setState({
      accountBurnrateEnabled: true,
      accountViewMode: "cards",
      accountListSort: null,
      initialized: true,
    });
  });

  function mockReadyDashboard(logsQueryOverrides: RequestLogsQueryOverrides = {}) {
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
        isLoading: false,
        isPending: false,
        isSuccess: true,
        refetch: vi.fn(),
        ...logsQueryOverrides,
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

    return overview;
  }

  it("keeps the page-wide skeleton while overview data is unavailable", () => {
    mockReadyDashboard();
    useDashboardMock.mockReturnValue({
      data: undefined,
      isFetching: true,
      error: null,
    } as ReturnType<typeof useDashboard>);

    renderWithProviders(<DashboardPage />);

    expect(screen.getByTestId("dashboard-skeleton")).toBeInTheDocument();
    expect(screen.queryByTestId("stats-grid")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Accounts" })).not.toBeInTheDocument();
  });

  it("renders request-log loading inside its section without hiding overview content", () => {
    mockReadyDashboard({
      data: undefined,
      error: null,
      isFetching: true,
      isLoading: true,
      isPending: true,
      isSuccess: false,
    });

    renderWithProviders(<DashboardPage />);

    expect(screen.queryByTestId("dashboard-skeleton")).not.toBeInTheDocument();
    expect(screen.getByTestId("stats-grid")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Accounts" })).toBeInTheDocument();
    const requestLogsSection = screen.getByRole("heading", { name: "Request Logs" }).closest("section");

    expect(requestLogsSection).not.toBeNull();
    expect(within(requestLogsSection as HTMLElement).getByText("Loading...")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reset/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId("recent-requests-table")).not.toBeInTheDocument();
  });

  it("renders the account summary line in the Accounts header using overview accounts", () => {
    const overview = mockReadyDashboard();

    renderWithProviders(<DashboardPage />);

    const accountsHeader = screen.getByRole("heading", { name: "Accounts" }).parentElement;

    expect(accountsHeader).not.toBeNull();
    expect(within(accountsHeader as HTMLElement).getByTestId("account-summary-line")).toHaveTextContent(
      "Summary for 2 accounts",
    );
    expect(accountSummaryLineSpy).toHaveBeenCalledWith(overview.accounts);
  });

  it("defaults the Accounts section to card view", () => {
    const overview = mockReadyDashboard();

    renderWithProviders(<DashboardPage />);

    expect(screen.getByTestId("account-cards")).toHaveTextContent("Cards for 2 accounts");
    expect(screen.queryByTestId("account-list")).not.toBeInTheDocument();
    expect(accountCardsSpy).toHaveBeenCalledWith(overview.accounts);
    expect(screen.getByRole("radio", { name: "View accounts as cards" })).toHaveAttribute("aria-checked", "true");
  });

  it("switches the Accounts section to list view", async () => {
    const user = userEvent.setup();
    const overview = mockReadyDashboard();

    renderWithProviders(<DashboardPage />);

    await user.click(screen.getByRole("radio", { name: "View accounts as list" }));

    expect(screen.getByTestId("account-list")).toHaveTextContent("List for 2 accounts");
    expect(screen.queryByTestId("account-cards")).not.toBeInTheDocument();
    expect(accountListSpy).toHaveBeenCalledWith({ accounts: overview.accounts, sort: null });
    expect(useDashboardPreferencesStore.getState().accountViewMode).toBe("list");
  });

  it("passes persisted account list sort through and updates it from the list", async () => {
    const user = userEvent.setup();
    const overview = mockReadyDashboard();
    useDashboardPreferencesStore.setState({
      accountBurnrateEnabled: true,
      accountViewMode: "list",
      accountListSort: { key: "quota", direction: "asc" },
      initialized: true,
    });

    renderWithProviders(<DashboardPage />);

    expect(screen.getByTestId("account-list")).toHaveTextContent("List for 2 accounts");
    expect(accountListSpy).toHaveBeenCalledWith({
      accounts: overview.accounts,
      sort: { key: "quota", direction: "asc" },
    });

    await user.click(screen.getByTestId("account-list"));

    expect(useDashboardPreferencesStore.getState().accountListSort).toEqual({ key: "credits", direction: "desc" });
  });

  it("renders conversation badge between Statuses and Reset in filters", () => {
    mockReadyDashboard();
    const updateFilters = vi.fn();
    useRequestLogsMock.mockReturnValue({
      filters: {
        search: "",
        timeframe: "all",
        accountIds: [],
        apiKeyIds: [],
        modelOptions: [],
        statuses: ["ok"],
        conversationId: "conv_page_badge",
        limit: 25,
        offset: 0,
      },
      listFilters: { search: undefined, limit: 25, offset: 0, accountIds: [], apiKeyIds: [], statuses: ["ok"], modelOptions: [], since: undefined },
      facetFilters: { since: undefined, accountIds: [], apiKeyIds: [], modelOptions: [] },
      logsQuery: {
        data: { requests: [], total: 0, hasMore: false, conversation: null },
        isFetching: false,
        error: null,
        isLoading: false,
        isPending: false,
        isSuccess: true,
        refetch: vi.fn(),
      },
      optionsQuery: {
        data: { accountIds: [], apiKeys: [], modelOptions: [], statuses: ["ok", "error"] },
        error: null,
      },
      updateFilters,
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

    // Badge text appears with the conversation ID
    expect(screen.getByText(/conv_page_badge/)).toBeInTheDocument();
    // Reset button exists
    expect(screen.getByRole("button", { name: /reset/i })).toBeInTheDocument();
    // Dismiss button exists
    expect(screen.getByRole("button", { name: /remove conversation/i })).toBeInTheDocument();
  });

  it("renders conversation summary between filters and table when conversation data present", () => {
    mockReadyDashboard();
    const updateFilters = vi.fn();

    useRequestLogsMock.mockReturnValue({
      filters: {
        search: "quota",
        timeframe: "24h",
        accountIds: ["acc_primary"],
        apiKeyIds: ["key_1"],
        modelOptions: ["gpt-5.1:::high"],
        statuses: ["ok"],
        conversationId: "conv_page_summary",
        limit: 25,
        offset: 0,
      },
      listFilters: { search: "quota", limit: 25, offset: 0, accountIds: ["acc_primary"], apiKeyIds: ["key_1"], statuses: ["ok"], modelOptions: ["gpt-5.1:::high"], since: expect.any(String) as string, conversationId: "conv_page_summary" },
      facetFilters: { since: expect.any(String) as string, accountIds: ["acc_primary"], apiKeyIds: ["key_1"], modelOptions: ["gpt-5.1:::high"] },
      logsQuery: {
        data: {
          requests: [],
          total: 0,
          hasMore: false,
          conversation: { requestCount: 42, aggregatedCostUsd: 3.14 },
        },
        isFetching: false,
        error: null,
        isLoading: false,
        isPending: false,
        isSuccess: true,
        refetch: vi.fn(),
      },
      optionsQuery: {
        data: {
          accountIds: ["acc_primary"],
          apiKeys: [{ id: "key_1", name: "Primary Key" }],
          modelOptions: [{ model: "gpt-5.1", reasoningEffort: "high" }],
          statuses: ["ok"],
        },
        error: null,
      },
      updateFilters,
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

    // Summary sentence contains count and cost — text split by Trans/code elements
    const summaryEls = screen.getAllByText((_, el) => el?.textContent?.includes("The conversation conv_page_summary") ?? false);
    const summaryEl = summaryEls[summaryEls.length - 1];
    expect(summaryEl).toBeInTheDocument();

    // Rendered text contains cost = with no literal backticks
    expect(summaryEl.textContent).toMatch(/\bcost = /);
    expect(summaryEl.textContent).not.toMatch(/`cost =`/);

    // Exactly three <code> elements with expected values
    const codeElements = summaryEl.querySelectorAll("code");
    expect(codeElements).toHaveLength(3);
    expect(codeElements[0].textContent).toBe("conv_page_summary");
    expect(codeElements[1].textContent).toBe("42");
    expect(codeElements[2].textContent).toBe("$3.14");

    // Prove summary is between filters and table via DOM order
    const requestLogsSection = screen.getByRole("heading", { name: "Request Logs" }).closest("section");
    expect(requestLogsSection).not.toBeNull();
    const allSectionElements = Array.from((requestLogsSection as HTMLElement).querySelectorAll("div.rounded-xl, [data-testid]"));
    const filterIdx = allSectionElements.findIndex((c) => c.getAttribute("class")?.includes("rounded-xl") && c.getAttribute("class")?.includes("bg-card"));
    const summaryIdx = allSectionElements.findIndex((c) => c.textContent?.includes("The conversation"));
    const tableIdx = allSectionElements.findIndex((c) => c.getAttribute("data-testid") === "recent-requests-table");

    expect(filterIdx).toBeGreaterThan(-1);
    expect(summaryIdx).toBeGreaterThan(-1);
    expect(tableIdx).toBeGreaterThan(-1);
    expect(filterIdx).toBeLessThan(summaryIdx);
    expect(summaryIdx).toBeLessThan(tableIdx);

    // Suffix contains localized filter labels
    expect(summaryEl.textContent).toMatch(/24h/);
    expect(summaryEl.textContent).toMatch(/OK/i);
    // Uses decoded model label, not raw model:::effort
    expect(summaryEl.textContent).toMatch(/gpt-5\.1 \(high\)/);
    // Uses account display name from overview accounts
    expect(summaryEl.textContent).toMatch(/primary@example\.com/);
    // Uses user-facing API key name, not raw ID
    expect(summaryEl.textContent).toMatch(/Primary Key/);
    // Uses search value, not raw API key ID
    expect(summaryEl.textContent).toMatch(/"quota"/);
    // No raw IDs or internal encodings leak
    expect(summaryEl.textContent).not.toMatch(/acc_primary/);
    expect(summaryEl.textContent).not.toMatch(/key_1/);
    expect(summaryEl.textContent).not.toMatch(/:::/);
  });

  it("never exposes raw IDs when option lists are empty or missing", () => {
    mockReadyDashboard();
    const updateFilters = vi.fn();

    useRequestLogsMock.mockReturnValue({
      filters: {
        search: "test_search",
        timeframe: "7d",
        accountIds: ["acc_missing"],
        apiKeyIds: ["key_missing"],
        modelOptions: ["gpt-5.1:::high"],
        statuses: ["error"],
        conversationId: "conv_safety",
        limit: 25,
        offset: 0,
      },
      listFilters: { search: "test_search", limit: 25, offset: 0, accountIds: ["acc_missing"], apiKeyIds: ["key_missing"], statuses: ["error"], modelOptions: ["gpt-5.1:::high"], since: expect.any(String) as string, conversationId: "conv_safety" },
      facetFilters: { since: expect.any(String) as string, accountIds: ["acc_missing"], apiKeyIds: [], modelOptions: [] },
      logsQuery: {
        data: {
          requests: [],
          total: 0,
          hasMore: false,
          conversation: { requestCount: 3, aggregatedCostUsd: 0.01 },
        },
        isFetching: false,
        error: null,
        isLoading: false,
        isPending: false,
        isSuccess: true,
        refetch: vi.fn(),
      },
      optionsQuery: {
        data: { accountIds: [], apiKeys: [], modelOptions: [], statuses: ["error"] },
        error: null,
      },
      updateFilters,
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

    const summaryEls = screen.getAllByText((_, el) => el?.textContent?.includes("The conversation conv_safety") ?? false);
    const summaryEl = summaryEls[summaryEls.length - 1];
    // Must never leak raw internal IDs
    expect(summaryEl.textContent).not.toMatch(/acc_missing/);
    expect(summaryEl.textContent).not.toMatch(/key_missing/);
    expect(summaryEl.textContent).not.toMatch(/:::/);
    // Model must be decoded from the filter value itself via formatModelLabel
    expect(summaryEl.textContent).toMatch(/gpt-5\.1\s+\(high\)/);
    // Timeframe, status, search render normally
    expect(summaryEl.textContent).toMatch(/7d/);
    expect(summaryEl.textContent).toMatch(/Error/i);
    expect(summaryEl.textContent).toMatch(/"test_search"/);
    // Safe fallback labels used when options missing
    expect(summaryEl.textContent).toMatch(/Accounts/);
    expect(summaryEl.textContent).toMatch(/API Keys/);
  });

  it("omits summary suffix when no other filters are active", () => {
    mockReadyDashboard();
    const updateFilters = vi.fn();

    useRequestLogsMock.mockReturnValue({
      filters: {
        search: "",
        timeframe: "all",
        accountIds: [],
        apiKeyIds: [],
        modelOptions: [],
        statuses: [],
        conversationId: "conv_no_suffix",
        limit: 25,
        offset: 0,
      },
      listFilters: { search: undefined, limit: 25, offset: 0, accountIds: [], apiKeyIds: [], statuses: [], modelOptions: [], since: undefined },
      facetFilters: { since: undefined, accountIds: [], apiKeyIds: [], modelOptions: [] },
      logsQuery: {
        data: {
          requests: [],
          total: 0,
          hasMore: false,
          conversation: { requestCount: 7, aggregatedCostUsd: 0.05 },
        },
        isFetching: false,
        error: null,
        isLoading: false,
        isPending: false,
        isSuccess: true,
        refetch: vi.fn(),
      },
      optionsQuery: {
        data: { accountIds: [], apiKeys: [], modelOptions: [], statuses: [] },
        error: null,
      },
      updateFilters,
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

    const summaryEl = screen.getByText((_, el) => el?.tagName === "P" && (el?.textContent?.includes("The conversation conv_no_suffix") ?? false));
    expect(summaryEl).toBeInTheDocument();
    // No filter suffix separator
    expect(summaryEl.textContent).not.toMatch(/filters:/i);
    expect(summaryEl.textContent).toMatch(/request\(s\), cost =/);
  });

  it("dismiss button clears conversationId and resets offset, preserving other filters", () => {
    mockReadyDashboard();
    const updateFilters = vi.fn();

    useRequestLogsMock.mockReturnValue({
      filters: {
        search: "test",
        timeframe: "7d",
        accountIds: ["acc_primary"],
        apiKeyIds: [],
        modelOptions: [],
        statuses: ["ok"],
        conversationId: "conv_dismiss_preserve",
        limit: 25,
        offset: 0,
      },
      listFilters: { search: "test", limit: 25, offset: 0, accountIds: ["acc_primary"], apiKeyIds: [], statuses: ["ok"], modelOptions: [], since: expect.any(String) as string },
      facetFilters: { since: expect.any(String) as string, accountIds: ["acc_primary"], apiKeyIds: [], modelOptions: [] },
      logsQuery: {
        data: {
          requests: [],
          total: 0,
          hasMore: false,
          conversation: { requestCount: 1, aggregatedCostUsd: 0.01 },
        },
        isFetching: false,
        error: null,
        isLoading: false,
        isPending: false,
        isSuccess: true,
        refetch: vi.fn(),
      },
      optionsQuery: {
        data: { accountIds: ["acc_primary"], apiKeys: [], modelOptions: [], statuses: ["ok"] },
        error: null,
      },
      updateFilters,
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

    const dismissButton = screen.getByRole("button", { name: /remove conversation/i });
    fireEvent.click(dismissButton);

    // Should call updateFilters with conversationId:null and offset:0 only
    expect(updateFilters).toHaveBeenCalledWith({ conversationId: null, offset: 0 });
  });

  it("Reset button clears conversation plus all filters", () => {
    mockReadyDashboard();
    const updateFilters = vi.fn();

    useRequestLogsMock.mockReturnValue({
      filters: {
        search: "test",
        timeframe: "7d",
        accountIds: ["acc_primary"],
        apiKeyIds: ["key_1"],
        modelOptions: ["gpt-5.1:::high"],
        statuses: ["ok"],
        conversationId: "conv_reset_all",
        limit: 25,
        offset: 5,
      },
      listFilters: { search: "test", limit: 25, offset: 5, accountIds: ["acc_primary"], apiKeyIds: ["key_1"], statuses: ["ok"], modelOptions: ["gpt-5.1:::high"], since: expect.any(String) as string },
      facetFilters: { since: expect.any(String) as string, accountIds: ["acc_primary"], apiKeyIds: ["key_1"], modelOptions: ["gpt-5.1:::high"] },
      logsQuery: {
        data: {
          requests: [],
          total: 0,
          hasMore: false,
          conversation: { requestCount: 1, aggregatedCostUsd: 0.01 },
        },
        isFetching: false,
        error: null,
        isLoading: false,
        isPending: false,
        isSuccess: true,
        refetch: vi.fn(),
      },
      optionsQuery: {
        data: { accountIds: ["acc_primary"], apiKeys: [{ id: "key_1", name: "Primary Key" }], modelOptions: [{ model: "gpt-5.1", reasoningEffort: "high" }], statuses: ["ok"] },
        error: null,
      },
      updateFilters,
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

    const resetButton = screen.getByRole("button", { name: /reset/i });
    fireEvent.click(resetButton);

    expect(updateFilters).toHaveBeenCalledWith({
      search: "",
      timeframe: "all",
      accountIds: [],
      apiKeyIds: [],
      modelOptions: [],
      statuses: [],
      conversationId: null,
      offset: 0,
    });
  });
});
