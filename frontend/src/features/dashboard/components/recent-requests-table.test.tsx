import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";
import { renderWithProviders } from "@/test/utils";

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
  it("shows Fast only for effective priority rows and opens the drawer on row click", async () => {
    const user = userEvent.setup();

    renderWithProviders(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[
          {
            accountId: "acc-primary",
            email: "primary@example.com",
            displayName: "Primary Account",
            planType: "plus",
            status: "active",
            additionalQuotas: [],
          },
        ]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-primary",
            apiKeyName: "Key Alpha",
            requestId: "req_1",
            model: "gpt-5.1",
            serviceTier: "priority",
            requestedServiceTier: "priority",
            actualServiceTier: "priority",
            transport: "websocket",
            status: "ok",
            errorCode: null,
            errorMessage: null,
            tokens: 1200,
            cachedInputTokens: 200,
            reasoningEffort: "high",
            costUsd: 0.01,
            latencyMs: 1000,
          },
          {
            requestedAt: ISO,
            accountId: "acc-primary",
            apiKeyName: "Key Beta",
            requestId: "req_2",
            model: "gpt-5.1",
            serviceTier: "default",
            requestedServiceTier: "priority",
            actualServiceTier: "default",
            transport: "http",
            status: "ok",
            errorCode: null,
            errorMessage: null,
            tokens: 20,
            cachedInputTokens: null,
            reasoningEffort: null,
            costUsd: 0.001,
            latencyMs: 20,
          },
        ]}
      />,
    );

    expect(screen.getAllByText("Primary Account")).toHaveLength(2);
    expect(screen.getByText("Key Alpha")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.1 (high)")).toBeInTheDocument();
    expect(screen.queryByText("gpt-5.1 (high, priority)")).not.toBeInTheDocument();
    expect(screen.queryByText(/default/)).not.toBeInTheDocument();
    expect(screen.getByText("WS")).toBeInTheDocument();
    expect(screen.getAllByText("Fast")).toHaveLength(1);
    expect(screen.queryByText("Requested priority")).not.toBeInTheDocument();

    const requestedPriorityIcons = screen.getAllByRole("img", { name: "Priority requested" });
    expect(requestedPriorityIcons).toHaveLength(2);
    await user.hover(requestedPriorityIcons[1]);
    expect(
      (await screen.findAllByText("Priority requested for this request. If granted, pricing and quota usage increase.")).length,
    ).toBeGreaterThan(0);

    await user.click(screen.getByText("Key Alpha"));
    expect(await screen.findByText("Request visibility")).toBeInTheDocument();
    expect(await screen.findByText("Request headers")).toBeInTheDocument();
    expect(screen.getAllByText("Fast").length).toBeGreaterThanOrEqual(2);
  });

  it("supports error expansion without opening the drawer", async () => {
    const user = userEvent.setup();
    const longError = "Rate limit reached while processing this request ".repeat(3);

    renderWithProviders(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[]}
        requests={[
          {
            requestedAt: ISO,
            accountId: null,
            apiKeyName: null,
            requestId: "req_1",
            model: "gpt-5.1",
            serviceTier: "default",
            requestedServiceTier: null,
            actualServiceTier: "default",
            transport: "http",
            status: "rate_limit",
            errorCode: "rate_limit_exceeded",
            errorMessage: longError,
            tokens: 10,
            cachedInputTokens: null,
            reasoningEffort: null,
            costUsd: 0.01,
            latencyMs: 50,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "View" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("Error Detail")).toBeInTheDocument();
    expect(dialog.textContent).toContain("Rate limit reached while processing this request");
    expect(screen.queryByText("Request visibility")).not.toBeInTheDocument();
  });

  it("renders empty state", () => {
    renderWithProviders(<RecentRequestsTable {...PAGINATION_PROPS} total={0} accounts={[]} requests={[]} />);
    expect(screen.getByText("No request logs match the current filters.")).toBeInTheDocument();
  });

  it("renders placeholder transport for legacy rows", () => {
    renderWithProviders(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-legacy",
            apiKeyName: null,
            requestId: "req-legacy",
            model: "gpt-5.1",
            serviceTier: null,
            requestedServiceTier: null,
            actualServiceTier: null,
            transport: null,
            status: "ok",
            errorCode: null,
            errorMessage: null,
            tokens: 1,
            cachedInputTokens: null,
            reasoningEffort: null,
            costUsd: 0,
            latencyMs: 1,
          },
        ]}
      />,
    );

    expect(screen.getAllByText("--")[0]).toBeInTheDocument();
  });
});
