import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RecentRequestsTable } from "@/features/dashboard/components/recent-requests-table";

const ISO = "2026-01-01T12:00:00+00:00";

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
  },
}));

const PAGINATION_PROPS = {
  total: 1,
  limit: 25,
  offset: 0,
  hasMore: false,
  onLimitChange: vi.fn(),
  onOffsetChange: vi.fn(),
};

describe("RecentRequestsTable", () => {
  beforeEach(() => {
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("renders rows with status badges and supports request details and copy actions", async () => {
    const longError = "Rate limit reached while processing this request ".repeat(3);
    const writeText = vi.fn().mockResolvedValue(undefined);

    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

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
             additionalQuotas: [],
           },
         ]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-primary",
            planType: "plus",
            apiKeyName: "Key Alpha",
            requestId: "req-1",
            model: "gpt-5.1",
            serviceTier: "default",
            requestedServiceTier: "priority",
            actualServiceTier: "default",
            transport: "websocket",
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
    expect(screen.getAllByText("Plus")[0]).toBeInTheDocument();
    expect(screen.getByText("Key Alpha")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.1 (high, default)")).toBeInTheDocument();
    expect(screen.getByText("Requested priority")).toBeInTheDocument();
    expect(screen.getByText("WS")).toBeInTheDocument();
    expect(screen.getByText("Rate limit")).toBeInTheDocument();
    expect(screen.getByText("rate_limit_exceeded")).toBeInTheDocument();

    const viewButton = screen.getByRole("button", { name: "View Details" });
    fireEvent.click(viewButton);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("Request Details")).toBeInTheDocument();
    expect(screen.getByText("req-1")).toBeInTheDocument();
    expect(screen.getAllByText("rate_limit_exceeded")[0]).toBeInTheDocument();
    expect(dialog.textContent).toContain("Rate limit reached while processing this request");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy Request ID" }));
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledWith("req-1");
    expect(toastSuccess).toHaveBeenCalledWith("Copied to clipboard");
    expect(screen.getByRole("button", { name: "Copy Request ID Copied" })).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy Error" }));
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledWith(longError);
  });

  it("renders empty state", () => {
    render(<RecentRequestsTable {...PAGINATION_PROPS} total={0} accounts={[]} requests={[]} />);
    expect(screen.getByText("No request logs match the current filters.")).toBeInTheDocument();
  });

  it("renders placeholder transport for legacy rows", () => {
    render(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-legacy",
            planType: null,
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

  it("shows details action for error-code-only rows", async () => {
    render(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-legacy",
            planType: null,
            apiKeyName: null,
            requestId: "req-error-code",
            model: "gpt-5.1",
            serviceTier: null,
            requestedServiceTier: null,
            actualServiceTier: null,
            transport: "http",
            status: "error",
            errorCode: "upstream_error",
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

    expect(screen.getAllByText("upstream_error")[0]).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View Details" }));

    expect(screen.getByRole("dialog")).toHaveTextContent("upstream_error");
    expect(screen.getByRole("dialog")).toHaveTextContent("Full Error");
  });
});
