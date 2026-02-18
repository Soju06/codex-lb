import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";

const ISO = "2026-01-01T12:00:00+00:00";

const PAGINATION_PROPS = {
  total: 1,
  limit: 25,
  offset: 0,
  hasMore: false,
  onLimitChange: vi.fn(),
  onOffsetChange: vi.fn(),
};

describe("RecentRequestsTable", () => {
  it("renders rows with status badges and supports error expansion", async () => {
    const user = userEvent.setup();
    const longError = "Rate limit reached while processing this request ".repeat(3);

    render(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[
          {
            accountId: "acc-primary",
            email: "primary@example.com",
            displayName: "Primary Account",
            planType: "plus",
            status: "active",
          },
        ]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-primary",
            requestId: "req-1",
            model: "gpt-5.1",
            status: "rate_limit",
            errorCode: "rate_limit_exceeded",
            errorMessage: longError,
            tokens: 1200,
            cachedInputTokens: 200,
            reasoningEffort: "high",
            costUsd: 0.01,
            latencyMs: 1000,
          },
        ]}
      />,
    );

    expect(screen.getByText("Primary Account")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.1 (high)")).toBeInTheDocument();
    expect(screen.getByText("Rate limit")).toBeInTheDocument();

    const viewButton = screen.getByRole("button", { name: "View" });
    await user.click(viewButton);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("Error Detail")).toBeInTheDocument();
    expect(dialog.textContent).toContain("Rate limit reached while processing this request");
  });

  it("renders empty state", () => {
    render(<RecentRequestsTable {...PAGINATION_PROPS} total={0} accounts={[]} requests={[]} />);
    expect(screen.getByText("No request logs match the current filters.")).toBeInTheDocument();
  });
});
