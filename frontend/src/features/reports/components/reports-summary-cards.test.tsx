// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ReportSummary } from "../schemas";
import {
  ReportsSummaryCards as ReportsSummaryCardsImpl,
  type ReportsSummaryCardsProps,
} from "./reports-summary-cards";

type ReportsSummaryFixture = Omit<
  ReportSummary,
  "totalCancelled" | "totalReasoningTokens" | "reasoningUsageKnownRequests"
> & {
  totalCancelled?: number;
  totalReasoningTokens?: number;
  reasoningUsageKnownRequests?: number;
};

function ReportsSummaryCards({
  summary,
  ...props
}: Omit<ReportsSummaryCardsProps, "summary"> & { summary: ReportsSummaryFixture }) {
  return (
    <ReportsSummaryCardsImpl
      {...props}
      summary={{
        totalCancelled: 0,
        totalReasoningTokens: 0,
        reasoningUsageKnownRequests: 0,
        ...summary,
      }}
    />
  );
}

describe("ReportsSummaryCards", () => {
  it("renders inline comparison badges for cost, tokens, and requests", () => {
    render(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 15,
          totalInputTokens: 1_600_000_000,
          totalOutputTokens: 13_000_000,
          totalReasoningTokens: 8_000_000,
          reasoningUsageKnownRequests: 1400,
          totalCachedTokens: 990_000_000,
          totalRequests: 1500,
          totalErrors: 0,
          totalConversations: 0,
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

    const costCard = screen.getByTestId("report-summary-card-total-cost");
    expect(within(costCard).getByText("▲ 50%")).toHaveClass(
      "text-emerald-600",
      "dark:text-emerald-400",
    );

    const tokensCard = screen.getByTestId("report-summary-card-tokens");
    expect(within(tokensCard).getByText("▼ 50%")).toHaveClass(
      "text-red-600",
      "dark:text-red-400",
    );

    const requestsCard = screen.getByTestId("report-summary-card-requests");
    expect(within(requestsCard).getByText("▲ 50%")).toHaveClass(
      "text-emerald-600",
      "dark:text-emerald-400",
    );

    expect(
      within(tokensCard).getByText(
        "Input 1.6B · Cache 990M · Output 13.0M",
      ),
    ).toBeInTheDocument();
    expect(within(tokensCard).getByText("Reported reasoning 8.0M (included in output) · 1400/1500 requests")).toBeInTheDocument();
    expect(tokensCard.parentElement).toHaveClass("lg:grid-cols-3", "xl:grid-cols-6");
    expect(within(requestsCard).getByText("avg 500/day · 3 accounts")).toBeInTheDocument();
  });

  it("shows reported reasoning coverage without adding reasoning to the token total", () => {
    render(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 1,
          totalInputTokens: 100,
          totalOutputTokens: 40,
          totalReasoningTokens: 30,
          reasoningUsageKnownRequests: 2,
          totalCachedTokens: 10,
          totalRequests: 4,
          totalErrors: 0,
          totalConversations: 1,
          activeAccounts: 1,
          avgCostPerDay: 1,
          avgRequestsPerDay: 4,
        }}
        comparison={{
          canCompare: false,
          previous: { totalCostUsd: 0, totalTokens: 0, totalRequests: 0 },
        }}
      />,
    );

    const tokensCard = screen.getByTestId("report-summary-card-tokens");
    expect(within(tokensCard).getByText("140")).toBeInTheDocument();
    expect(
      within(tokensCard).getByText(
        "Input 100 · Cache 10 · Output 40",
      ),
    ).toBeInTheDocument();
    expect(within(tokensCard).getByText("Reported reasoning 30 (included in output) · 2/4 requests")).toBeInTheDocument();
    expect(within(tokensCard).queryByText("170")).not.toBeInTheDocument();
  });

  it("renders grouped currency for full-value Cost displays", () => {
    render(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 1400,
          totalInputTokens: 0,
          totalOutputTokens: 0,
          totalCachedTokens: 0,
          totalRequests: 0,
          totalErrors: 0,
          totalConversations: 0,
          activeAccounts: 0,
          avgCostPerDay: 1400,
          avgRequestsPerDay: 0,
        }}
        comparison={{ canCompare: false, previous: { totalCostUsd: 0, totalTokens: 0, totalRequests: 0 } }}
      />,
    );

    const costCard = screen.getByTestId("report-summary-card-total-cost");
    expect(within(costCard).getByText("$1,400.00")).toBeInTheDocument();
    expect(costCard).toHaveTextContent("avg $1,400.00/day");
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
          totalConversations: 0,
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
          totalConversations: 0,
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
      within(screen.getByTestId("report-summary-card-total-cost")).queryByText(/^[▲▼] \d+%$/),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByTestId("report-summary-card-tokens")).queryByText(/^[▲▼] \d+%$/),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByTestId("report-summary-card-requests")).getByText("▲ 50%"),
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
          totalConversations: 0,
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


  it("renders Conversations card immediately after Requests with distinctive value", () => {
    render(
      <ReportsSummaryCards
        summary={{ totalCostUsd: 15, totalInputTokens: 300, totalOutputTokens: 150, totalCachedTokens: 0, totalRequests: 1500, totalErrors: 0, totalConversations: 42, activeAccounts: 3, avgCostPerDay: 5, avgRequestsPerDay: 500 }}
        comparison={{ canCompare: false, previous: { totalCostUsd: 0, totalTokens: 0, totalRequests: 0 } }}
      />,
    );
    const conversationsCard = screen.getByTestId("report-summary-card-conversations");
    expect(conversationsCard).toBeInTheDocument();
    expect(within(conversationsCard).getByText("Active Conversations")).toBeInTheDocument();
    expect(within(conversationsCard).getByText("42")).toBeInTheDocument();
    expect(within(conversationsCard).queryByText("42 distinct")).not.toBeInTheDocument();
    const requestsCard = screen.getByTestId("report-summary-card-requests");
    expect(requestsCard.nextElementSibling).toBe(conversationsCard);
  });

  it("renders requests, cancelled, and errors as distinct summary totals", () => {
    render(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 1,
          totalInputTokens: 100,
          totalOutputTokens: 20,
          totalCachedTokens: 0,
          totalRequests: 4,
          totalCancelled: 2,
          totalErrors: 1,
          totalConversations: 1,
          activeAccounts: 1,
          avgCostPerDay: 1,
          avgRequestsPerDay: 4,
        }}
        comparison={{
          canCompare: false,
          previous: { totalCostUsd: 0, totalTokens: 0, totalRequests: 0 },
        }}
      />,
    );

    const requestsCard = screen.getByTestId("report-summary-card-requests");
    expect(within(requestsCard).getByText("Requests")).toBeInTheDocument();
    expect(within(requestsCard).getByText("4")).toBeInTheDocument();

    const cancelledCard = screen.queryByTestId("report-summary-card-cancelled");
    expect.soft(cancelledCard).toBeInTheDocument();
    if (cancelledCard) {
      expect.soft(within(cancelledCard).getByText("Cancelled")).toBeInTheDocument();
      expect.soft(within(cancelledCard).getByText("2")).toBeInTheDocument();
    }

    const errorsCard = screen.queryByTestId("report-summary-card-errors");
    expect.soft(errorsCard).toBeInTheDocument();
    if (errorsCard) {
      expect.soft(within(errorsCard).getByText("Errors")).toBeInTheDocument();
      expect.soft(within(errorsCard).getByText("1")).toBeInTheDocument();
    }
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
          totalConversations: 0,
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

    const tokensCard = screen.getByTestId("report-summary-card-tokens");
    const requestsCard = screen.getByTestId("report-summary-card-requests");

    expect(within(tokensCard).getByText("100.0B")).toBeInTheDocument();
    expect(
      within(tokensCard).getByText(
        "Input 100.0B · Cache 0 · Output 0",
      ),
    ).toBeInTheDocument();
    expect(within(tokensCard).getByText("Reported reasoning 0 (included in output) · 0/100000 requests")).toBeInTheDocument();
    expect(within(requestsCard).getByText("100.0K")).toBeInTheDocument();
  });

  it("hides reasoning coverage when there are no requests", () => {
    render(
      <ReportsSummaryCards
        summary={{
          totalCostUsd: 0,
          totalInputTokens: 0,
          totalOutputTokens: 0,
          totalCachedTokens: 0,
          totalRequests: 0,
          totalErrors: 0,
          totalConversations: 0,
          activeAccounts: 0,
          avgCostPerDay: 0,
          avgRequestsPerDay: 0,
        }}
        comparison={{
          canCompare: false,
          previous: { totalCostUsd: 0, totalTokens: 0, totalRequests: 0 },
        }}
      />,
    );

    expect(screen.queryByText(/Reported reasoning/)).not.toBeInTheDocument();
  });
});
