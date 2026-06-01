import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";
import type { ReportsResponse } from "@/features/reports/schemas";
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

describe("ReportsPage", () => {
  beforeEach(() => {
    useReportsMock.mockReset();
  });

  it("keeps model options from the unfiltered model catalog", async () => {
    const user = userEvent.setup();
    useReportsMock.mockImplementation((filters) => ({
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
    }) as ReturnType<typeof useReports>);

    renderWithProviders(<ReportsPage initialFilters={{ model: "gpt-5.1" }} />);

    await user.click(screen.getByRole("button", { name: /gpt-5.1/i }));

    expect(await screen.findByRole("menuitemcheckbox", { name: /gpt-5.2/i })).toBeInTheDocument();
  });
});
