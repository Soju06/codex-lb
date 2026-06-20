// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReportsSummaryCards } from "./reports-summary-cards";

describe("ReportsSummaryCards", () => {
  it("renders inline comparison badges for cost, tokens, and requests", () => {
    render(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 15,
          totalInputTokens: 1_600_000_000,
          totalOutputTokens: 13_000_000,
          totalCachedTokens: 990_000_000,
          totalRequests: 1500,
          totalErrors: 0,
          activeAccounts: 3,
          avgCostPerDay: 5,
          avgRequestsPerDay: 500,
        }}
        comparison={{
          canCompare: true,
          previous: {
            totalCostUsd: 10,
            totalTokens: 3_206_000_000,
            totalRequests: 1000,
          },
        }}
      />,
    );

    const costCard = screen.getByTestId("report-summary-card-Total Cost");
    expect(within(costCard).getByText("▲ 50%")).toHaveClass(
      "text-emerald-600",
      "dark:text-emerald-400",
    );

    const tokensCard = screen.getByTestId("report-summary-card-Tokens");
    expect(within(tokensCard).getByText("▼ 50%")).toHaveClass(
      "text-red-600",
      "dark:text-red-400",
    );

    const requestsCard = screen.getByTestId("report-summary-card-Requests");
    expect(within(requestsCard).getByText("▲ 50%")).toHaveClass(
      "text-emerald-600",
      "dark:text-emerald-400",
    );

    expect(
      within(tokensCard).getByText("Input 1.6B · Cache 990M · Output 13.0M"),
    ).toBeInTheDocument();
    expect(within(requestsCard).getByText("avg 500/day · 3 accounts")).toBeInTheDocument();
  });

  it("hides comparison badges when unavailable or previous totals are zero", () => {
    const { rerender } = render(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 15,
          totalInputTokens: 300,
          totalOutputTokens: 150,
          totalCachedTokens: 0,
          totalRequests: 1500,
          totalErrors: 0,
          activeAccounts: 3,
          avgCostPerDay: 5,
          avgRequestsPerDay: 500,
        }}
        comparison={{
          canCompare: false,
          previous: {
            totalCostUsd: 10,
            totalTokens: 900,
            totalRequests: 1000,
          },
        }}
      />,
    );

    expect(screen.queryByText(/^[▲▼] \d+%$/)).not.toBeInTheDocument();

    rerender(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 15,
          totalInputTokens: 300,
          totalOutputTokens: 150,
          totalCachedTokens: 0,
          totalRequests: 1500,
          totalErrors: 0,
          activeAccounts: 3,
          avgCostPerDay: 5,
          avgRequestsPerDay: 500,
        }}
        comparison={{
          canCompare: true,
          previous: {
            totalCostUsd: 0,
            totalTokens: 0,
            totalRequests: 1000,
          },
        }}
      />,
    );

    expect(
      within(screen.getByTestId("report-summary-card-Total Cost")).queryByText(/^[▲▼] \d+%$/),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByTestId("report-summary-card-Tokens")).queryByText(/^[▲▼] \d+%$/),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByTestId("report-summary-card-Requests")).getByText("▲ 50%"),
    ).toBeInTheDocument();
  });

  it("hides comparison badges when canCompare is true but all previous totals are zero", () => {
    render(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 15,
          totalInputTokens: 300,
          totalOutputTokens: 150,
          totalCachedTokens: 0,
          totalRequests: 1500,
          totalErrors: 0,
          activeAccounts: 3,
          avgCostPerDay: 5,
          avgRequestsPerDay: 500,
        }}
        comparison={{
          canCompare: true,
          previous: {
            totalCostUsd: 0,
            totalTokens: 0,
            totalRequests: 0,
          },
        }}
      />,
    );

    expect(screen.queryByText(/^[▲▼] \d+%$/)).not.toBeInTheDocument();
  });

  it("preserves trailing zeroes for unrelated whole K and B values", () => {
    render(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 15,
          totalInputTokens: 100_000_000_000,
          totalOutputTokens: 0,
          totalCachedTokens: 0,
          totalRequests: 100_000,
          totalErrors: 0,
          activeAccounts: 3,
          avgCostPerDay: 5,
          avgRequestsPerDay: 500,
        }}
        comparison={{
          canCompare: false,
          previous: {
            totalCostUsd: 0,
            totalTokens: 0,
            totalRequests: 0,
          },
        }}
      />,
    );

    const tokensCard = screen.getByTestId("report-summary-card-Tokens");
    const requestsCard = screen.getByTestId("report-summary-card-Requests");

    expect(within(tokensCard).getByText("100.0B")).toBeInTheDocument();
    expect(within(tokensCard).getByText("Input 100.0B · Cache 0 · Output 0")).toBeInTheDocument();
    expect(within(requestsCard).getByText("100.0K")).toBeInTheDocument();
  });
});
