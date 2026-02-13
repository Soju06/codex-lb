import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";

const ISO = "2026-01-01T12:00:00+00:00";

describe("RecentRequestsTable", () => {
  it("renders rows with status badges and supports error expansion", async () => {
    const user = userEvent.setup();
    const longError = "Rate limit reached while processing this request ".repeat(3);

    render(
      <RecentRequestsTable
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

    expect(screen.getByText("Recent Requests")).toBeInTheDocument();
    expect(screen.getByText("Primary Account")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.1 (high)")).toBeInTheDocument();
    expect(screen.getByText("Rate limit")).toBeInTheDocument();

    const expandButton = screen.getByRole("button", { name: "Expand" });
    await user.click(expandButton);
    expect(screen.getByRole("button", { name: "Collapse" })).toBeInTheDocument();
    expect(
      screen.getByText((content) =>
        content.includes("Rate limit reached while processing this request"),
      ),
    ).toBeInTheDocument();
  });

  it("renders empty state", () => {
    render(<RecentRequestsTable accounts={[]} requests={[]} />);
    expect(screen.getByText("No request logs match the current filters.")).toBeInTheDocument();
  });
});
