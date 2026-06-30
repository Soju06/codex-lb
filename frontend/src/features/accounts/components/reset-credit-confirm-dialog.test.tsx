import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { ResetCreditConfirmDialog } from "@/features/accounts/components/reset-credit-confirm-dialog";
import { server } from "@/test/mocks/server";

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

const SNAPSHOT_URL = "/api/accounts/acc_primary/rate-limit-reset-credits";
const CONSUME_URL = "/api/accounts/acc_primary/rate-limit-reset-credits/consume";

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

function renderWithClient(ui: ReactElement) {
  const queryClient = createTestQueryClient();
  const renderResult = render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
  return { queryClient, ...renderResult };
}

function snapshotResponse() {
  return HttpResponse.json({
    availableCount: 1,
    nearestExpiresAt: "2026-01-08T12:00:00.000Z",
    credits: [
      {
        id: "credit_soonest",
        status: "available",
        resetType: "rate_limit_reset",
        grantedAt: "2025-12-31T12:00:00.000Z",
        expiresAt: "2026-01-08T12:00:00.000Z",
        title: "Banked rate-limit reset",
        description: "Redeems a reset of the soonest rate-limit window.",
        redeemedAt: null,
        redeemStartedAt: null,
      },
    ],
  });
}

describe("ResetCreditConfirmDialog", () => {
  it("confirms and consumes the soonest reset credit, then invalidates queries", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const consumeCalled = vi.fn();
    server.use(
      http.get(SNAPSHOT_URL, snapshotResponse),
      http.post(CONSUME_URL, () => {
        consumeCalled();
        return HttpResponse.json({
          code: "rate_limit_reset",
          windowsReset: 1,
          redeemedAt: "2026-01-01T12:00:00.000Z",
        });
      }),
    );

    const { queryClient } = renderWithClient(
      <ResetCreditConfirmDialog
        open
        onOpenChange={onOpenChange}
        accountId="acc_primary"
      />,
    );
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    // Snapshot loads the available count and soonest credit expiry.
    expect(await screen.findByText("1 free rate limit reset")).toBeInTheDocument();
    expect(screen.getByText(/Reset expires on \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Redeem credit" }));

    await vi.waitFor(() => expect(consumeCalled).toHaveBeenCalledTimes(1));
    await vi.waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Rate-limit window reset (1)"),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "trends"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard", "projections"] });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("surfaces an error toast and does not invalidate when consume fails", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    server.use(
      http.get(SNAPSHOT_URL, snapshotResponse),
      http.post(CONSUME_URL, () =>
        HttpResponse.json(
          {
            error: {
              code: "no_reset_credit_available",
              message: "No reset credit available",
            },
          },
          { status: 409 },
        ),
      ),
    );

    const { queryClient } = renderWithClient(
      <ResetCreditConfirmDialog
        open
        onOpenChange={onOpenChange}
        accountId="acc_primary"
      />,
    );
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    expect(await screen.findByText("1 free rate limit reset")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Redeem credit" }));

    await vi.waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("No reset credit available"),
    );
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
    // Failure leaves the dialog open for retry.
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("shows a loading state while the reset-credit snapshot is fetching", () => {
    server.use(
      http.get(SNAPSHOT_URL, async () => {
        await new Promise(() => {});
        return snapshotResponse();
      }),
    );

    renderWithClient(
      <ResetCreditConfirmDialog
        open
        onOpenChange={vi.fn()}
        accountId="acc_primary"
      />,
    );

    expect(screen.getByText("Loading reset credit details...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redeem credit" })).toBeDisabled();
  });

  it("shows an error message and keeps confirm disabled when the snapshot fetch fails", async () => {
    server.use(
      http.get(SNAPSHOT_URL, () =>
        HttpResponse.json(
          {
            error: {
              code: "service_unavailable",
              message: "Reset credits unavailable",
            },
          },
          { status: 503 },
        ),
      ),
    );

    renderWithClient(
      <ResetCreditConfirmDialog
        open
        onOpenChange={vi.fn()}
        accountId="acc_primary"
      />,
    );

    expect(await screen.findByText("Reset credits unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redeem credit" })).toBeDisabled();
  });

  it("handles a null snapshot response without allowing redeem", async () => {
    server.use(http.get(SNAPSHOT_URL, () => HttpResponse.json(null)));

    renderWithClient(
      <ResetCreditConfirmDialog
        open
        onOpenChange={vi.fn()}
        accountId="acc_primary"
      />,
    );

    expect(await screen.findByText("0 free rate limit resets")).toBeInTheDocument();
    expect(screen.getByText("Reset credit details are not available yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redeem credit" })).toBeDisabled();
  });

  it("allows redeeming an available credit when expiry is null", async () => {
    const user = userEvent.setup();
    const consumeCalled = vi.fn();
    server.use(
      http.get(SNAPSHOT_URL, () =>
        HttpResponse.json({
          availableCount: 1,
          nearestExpiresAt: null,
          credits: [
            {
              id: "credit_no_expiry",
              status: "available",
              resetType: "rate_limit_reset",
              grantedAt: "2025-12-31T12:00:00.000Z",
              expiresAt: null,
              title: "Persistent banked reset",
              description: "Redeems a reset credit without an upstream expiry.",
              redeemedAt: null,
              redeemStartedAt: null,
            },
          ],
        }),
      ),
      http.post(CONSUME_URL, () => {
        consumeCalled();
        return HttpResponse.json({
          code: "rate_limit_reset",
          windowsReset: 1,
          redeemedAt: "2026-01-01T12:00:00.000Z",
        });
      }),
    );

    renderWithClient(
      <ResetCreditConfirmDialog
        open
        onOpenChange={vi.fn()}
        accountId="acc_primary"
      />,
    );

    expect(await screen.findByText("1 free rate limit reset")).toBeInTheDocument();
    expect(screen.getByText("No upcoming expiry data available.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Redeem credit" }));

    await vi.waitFor(() => expect(consumeCalled).toHaveBeenCalledTimes(1));
  });

  it("treats a loaded null snapshot as unavailable even when the summary count is stale", async () => {
    server.use(
      http.get(SNAPSHOT_URL, () => HttpResponse.json(null)),
    );

    renderWithClient(
      <ResetCreditConfirmDialog
        open
        onOpenChange={vi.fn()}
        accountId="acc_primary"
        summaryAvailableCount={2}
      />,
    );

    expect(await screen.findByText("0 free rate limit resets")).toBeInTheDocument();
    expect(screen.getByText("Reset credit details are not available yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redeem credit" })).toBeDisabled();
  });
});
