import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";
import type { ReportsResponse } from "@/features/reports/schemas";
import { listAccounts } from "@/features/accounts/api";
import { useReports } from "@/features/reports/hooks/use-reports";
import { ReportsPage } from "./reports-page";

vi.mock("@/features/accounts/api", () => ({
  listAccounts: vi.fn().mockResolvedValue({ accounts: [] }),
}));

vi.mock("@/features/reports/hooks/use-reports", () => ({
  useReports: vi.fn(),
}));

const EMPTY_REPORT: ReportsResponse = {
  summary: {
    totalCostUsd: 0,
    totalInputTokens: 0,
    totalOutputTokens: 0,
    totalCachedTokens: 0,
    totalRequests: 0,
    totalErrors: 0,
    activeAccounts: 0,
    avgCostPerDay: 0,
    avgRequestsPerDay: 0,
  },
  daily: [],
  byModel: [],
  byAccount: [],
};

const useReportsMock = vi.mocked(useReports);
const listAccountsMock = vi.mocked(listAccounts);
type UseReportsMockResult = ReturnType<typeof useReports>;

const asUseReportsResult = (
  value: Partial<UseReportsMockResult>,
): UseReportsMockResult => value as unknown as UseReportsMockResult;

describe("ReportsPage", () => {
  beforeEach(() => {
    useReportsMock.mockReset();
    listAccountsMock.mockReset();
    listAccountsMock.mockResolvedValue({ accounts: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("initializes default dates when the reports page mounts", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2030-01-15T12:00:00Z"));
    useReportsMock.mockReturnValue(
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );

    renderWithProviders(<ReportsPage />);

    expect(useReportsMock.mock.calls[0]?.[0]).toMatchObject({
      startDate: "2030-01-09",
      endDate: "2030-01-15",
    });
  });

  it("keeps model options from the unfiltered model catalog", async () => {
    const user = userEvent.setup();
    useReportsMock.mockImplementation((filters) =>
      asUseReportsResult({
        data: {
          ...EMPTY_REPORT,
          byModel: filters.model
            ? [{ model: "gpt-5.1", costUsd: 1, percentage: 100 }]
            : [
                { model: "gpt-5.1", costUsd: 1, percentage: 50 },
                { model: "gpt-5.2", costUsd: 1, percentage: 50 },
              ],
        },
        isLoading: false,
      }),
    );

    renderWithProviders(<ReportsPage initialFilters={{ model: "gpt-5.1" }} />);

    await user.click(screen.getByRole("button", { name: /gpt-5.1/i }));

    expect(
      await screen.findByRole("menuitemcheckbox", { name: /gpt-5.2/i }),
    ).toBeInTheDocument();
  });

  it("shows an error when report loading fails", async () => {
    useReportsMock.mockImplementation((filters) =>
      filters.model
        ? asUseReportsResult({
            isLoading: false,
            isError: true,
            error: new Error("report API unavailable"),
            refetch: vi.fn(),
            data: null as unknown as ReportsResponse,
          })
        : asUseReportsResult({
            data: EMPTY_REPORT,
            isLoading: false,
            isError: false,
            refetch: vi.fn(),
          }),
    );

    renderWithProviders(<ReportsPage initialFilters={{ model: "gpt-5.1" }} />);

    expect(
      await screen.findByText(
        /Failed to load report data: report API unavailable/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows model option load failures instead of hiding empty selector silently", async () => {
    useReportsMock.mockImplementation((filters) =>
      filters.model
        ? asUseReportsResult({
            data: {
              ...EMPTY_REPORT,
              byModel: [{ model: "gpt-5.1", costUsd: 1, percentage: 100 }],
            },
            isLoading: false,
            isError: false,
            refetch: vi.fn(),
          })
        : asUseReportsResult({
            isLoading: false,
            isError: true,
            error: new Error("model catalog endpoint unavailable"),
            refetch: vi.fn(),
            data: undefined,
          }),
    );

    renderWithProviders(<ReportsPage initialFilters={{ model: "gpt-5.1" }} />);

    expect(
      await screen.findByText(
        /Failed to load model options: model catalog endpoint unavailable/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /gpt-5.1/i }),
    ).toBeInTheDocument();
  });

  it("shows account option load failures instead of hiding empty selector silently", async () => {
    useReportsMock.mockImplementation(() =>
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );
    listAccountsMock.mockRejectedValueOnce(
      new Error("accounts backend timeout"),
    );

    renderWithProviders(<ReportsPage />);

    expect(
      await screen.findByText(
        /Failed to load account options: accounts backend timeout/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /accounts/i }),
    ).toBeInTheDocument();
  });
});
