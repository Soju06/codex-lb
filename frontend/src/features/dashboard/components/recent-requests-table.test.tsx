import { act, fireEvent, render, screen, within } from "@testing-library/react";
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

vi.mock("@/features/conversation-archive/components/request-archive-panel", () => ({
  RequestArchivePanel: ({ requestId }: { requestId: string }) => (
    <div data-testid="request-archive-panel">Archive for {requestId}</div>
  ),
}));

const PAGINATION_PROPS = {
  total: 1,
  limit: 25,
  offset: 0,
  hasMore: false,
  onLimitChange: vi.fn(),
  onOffsetChange: vi.fn(),
};

function openRequestDetails() {
  fireEvent.click(screen.getByRole("button", { name: "View Details" }));
  return screen.getByRole("dialog");
}

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
             limitWarmupEnabled: false,
             additionalQuotas: [],
           },
         ]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-primary",
            planType: "plus",
            apiKeyName: "Key Alpha",
            apiKeyId: "key-alpha",
            requestId: "req-1",
            model: "gpt-5.1",
            source: null,
            serviceTier: "default",
            requestedServiceTier: "priority",
            actualServiceTier: "default",
            transport: "websocket",
             status: "rate_limit",
             errorCode: "rate_limit_exceeded",
             errorMessage: longError,
             tokens: 1200,
             inputTokens: 1000,
             outputTokens: 200,
             cachedInputTokens: 200,
             reasoningEffort: "high",
             costUsd: 0.01,
             costBreakdown: {
               inputUsd: 0.004,
               cachedInputUsd: 0.001,
               outputUsd: 0.005,
               totalUsd: 0.01,
             },
             latencyMs: 1000,
           },
         ]}
       />,
    );

    expect(screen.getByText("Primary Account")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Plus" })).toBeInTheDocument();
    expect(screen.getByText("Key Alpha")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.1 (high, default)")).toBeInTheDocument();
    expect(screen.getByText("Requested priority")).toBeInTheDocument();
    expect(screen.getByText("WS")).toBeInTheDocument();
    expect(screen.getByText("Rate limit")).toBeInTheDocument();
    expect(screen.getByText("rate_limit_exceeded")).toBeInTheDocument();

    const dialog = openRequestDetails();
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText("Request Details")).toBeInTheDocument();
    expect(within(dialog).getByText("req-1")).toBeInTheDocument();
    expect(within(dialog).getByTestId("request-archive-panel")).toHaveTextContent("Archive for req-1");
    expect(within(dialog).getByText("rate_limit_exceeded")).toBeInTheDocument();
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
            apiKeyId: null,
            requestId: "req-legacy",
            model: "gpt-5.1",
            source: null,
            serviceTier: null,
            requestedServiceTier: null,
            actualServiceTier: null,
            transport: null,
             status: "ok",
             errorCode: null,
             errorMessage: null,
             tokens: 1,
             inputTokens: 1,
             outputTokens: 0,
             cachedInputTokens: null,
             reasoningEffort: null,
             costUsd: 0,
             costBreakdown: null,
             latencyMs: 1,
           },
         ]}
       />,
    );

    const row = screen.getByText("gpt-5.1").closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getAllByText("--").length).toBeGreaterThan(0);
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
            apiKeyId: null,
            requestId: "req-error-code",
            model: "gpt-5.1",
            source: null,
            serviceTier: null,
            requestedServiceTier: null,
            actualServiceTier: null,
            transport: "http",
             status: "error",
             errorCode: "upstream_error",
             errorMessage: null,
             tokens: 1,
             inputTokens: 1,
             outputTokens: 0,
             cachedInputTokens: null,
             reasoningEffort: null,
             costUsd: 0,
             costBreakdown: null,
             latencyMs: 1,
           },
         ]}
       />,
    );

    const dialog = openRequestDetails();

    expect(dialog).toHaveTextContent("upstream_error");
    expect(dialog).toHaveTextContent("Full Error");
  });

  it("shows a cost section for ok rows", () => {
    render(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-cost",
            planType: "plus",
            apiKeyName: "Key Cost",
            apiKeyId: "key-cost",
            requestId: "req-cost",
            model: "gpt-5.1",
            source: null,
            serviceTier: null,
            requestedServiceTier: null,
            actualServiceTier: null,
            transport: "http",
            status: "ok",
            errorCode: null,
            errorMessage: null,
            tokens: 1400,
            inputTokens: 1000,
            outputTokens: 400,
            cachedInputTokens: 200,
            reasoningEffort: null,
            costUsd: 0.01,
            costBreakdown: {
              inputUsd: 0.004,
              cachedInputUsd: 0.002,
              outputUsd: 0.004,
              totalUsd: 0.01,
            },
            latencyMs: 100,
          },
        ]}
      />,
    );

    const dialog = openRequestDetails();
    const costSection = within(dialog).getByText("Cost").closest("div.space-y-2");

    expect(within(dialog).getByText("Cost")).toBeInTheDocument();
    expect(costSection).toHaveTextContent("$0.01 =");
    expect(costSection).toHaveTextContent("800 Input ($0.00)");
    expect(costSection).toHaveTextContent("200 Cached ($0.00)");
    expect(costSection).toHaveTextContent("400 Output ($0.00)");
  });

  it("hides the cost section for non-ok rows", () => {
    render(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-no-cost",
            planType: null,
            apiKeyName: null,
            apiKeyId: null,
            requestId: "req-no-cost",
            model: "gpt-5.1",
            source: null,
            serviceTier: null,
            requestedServiceTier: null,
            actualServiceTier: null,
            transport: "http",
            status: "error",
            errorCode: "upstream_error",
            errorMessage: "boom",
            tokens: 1,
            inputTokens: 1,
            outputTokens: 0,
            cachedInputTokens: 0,
            reasoningEffort: null,
            costUsd: 0.01,
            costBreakdown: {
              inputUsd: 0.01,
              cachedInputUsd: null,
              outputUsd: null,
              totalUsd: 0.01,
            },
            latencyMs: 1,
          },
        ]}
      />,
    );

    const dialog = openRequestDetails();

    expect(within(dialog).queryByText("Cost")).not.toBeInTheDocument();
  });

  it("renders only available cost segments for partial data", () => {
    render(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-partial-cost",
            planType: "plus",
            apiKeyName: "Key Partial",
            apiKeyId: "key-partial",
            requestId: "req-partial-cost",
            model: "gpt-5.1",
            source: null,
            serviceTier: null,
            requestedServiceTier: null,
            actualServiceTier: null,
            transport: "http",
            status: "ok",
            errorCode: null,
            errorMessage: null,
            tokens: 700,
            inputTokens: 700,
            outputTokens: null,
            cachedInputTokens: 200,
            reasoningEffort: null,
            costUsd: 0.01,
            costBreakdown: {
              inputUsd: 0.006,
              cachedInputUsd: 0.004,
              outputUsd: null,
              totalUsd: 0.01,
            },
            latencyMs: 1,
          },
        ]}
      />,
    );

    const dialog = openRequestDetails();
    const costSection = within(dialog).getByText("Cost").closest("div.space-y-2");

    expect(within(dialog).getByText("Cost")).toBeInTheDocument();
    expect(costSection).toHaveTextContent("$0.01 =");
    expect(costSection).toHaveTextContent("500 Input ($0.01)");
    expect(costSection).toHaveTextContent("200 Cached ($0.00)");
    expect(costSection).not.toHaveTextContent("Output");
  });

  it("renders available cost segments when total cost is unavailable", () => {
    render(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-partial-no-total",
            planType: "plus",
            apiKeyName: "Key Partial No Total",
            apiKeyId: "key-partial-no-total",
            requestId: "req-partial-no-total",
            model: "gpt-5.1",
            source: null,
            serviceTier: null,
            requestedServiceTier: null,
            actualServiceTier: null,
            transport: "http",
            status: "ok",
            errorCode: null,
            errorMessage: null,
            tokens: null,
            inputTokens: 1000,
            outputTokens: null,
            cachedInputTokens: 200,
            reasoningEffort: null,
            costUsd: null,
            costBreakdown: {
              inputUsd: 0.006,
              cachedInputUsd: 0.004,
              outputUsd: null,
              totalUsd: null,
            },
            latencyMs: 1,
          },
        ]}
      />,
    );

    const dialog = openRequestDetails();
    const costSection = within(dialog).getByText("Cost").closest("div.space-y-2");

    expect(within(dialog).getByText("Cost")).toBeInTheDocument();
    expect(costSection).not.toHaveTextContent("=");
    expect(costSection).toHaveTextContent("800 Input ($0.01)");
    expect(costSection).toHaveTextContent("200 Cached ($0.00)");
    expect(costSection).not.toHaveTextContent("Output");
  });

  it("hides the cost section for total-only cost breakdown rows", () => {
    render(
      <RecentRequestsTable
        {...PAGINATION_PROPS}
        accounts={[]}
        requests={[
          {
            requestedAt: ISO,
            accountId: "acc-total-only-cost",
            planType: "plus",
            apiKeyName: "Key Total Only",
            apiKeyId: "key-total-only",
            requestId: "req-total-only-cost",
            model: "gpt-5.1",
            source: null,
            serviceTier: null,
            requestedServiceTier: null,
            actualServiceTier: null,
            transport: "http",
            status: "ok",
            errorCode: null,
            errorMessage: null,
            tokens: 1500,
            inputTokens: 1000,
            outputTokens: 500,
            cachedInputTokens: null,
            reasoningEffort: null,
            costUsd: 4.321234,
            costBreakdown: {
              inputUsd: null,
              cachedInputUsd: null,
              outputUsd: null,
              totalUsd: 4.321234,
            },
            latencyMs: 1,
          },
        ]}
      />,
    );

    const dialog = openRequestDetails();

    expect(within(dialog).queryByText("Cost")).not.toBeInTheDocument();
  });
});
