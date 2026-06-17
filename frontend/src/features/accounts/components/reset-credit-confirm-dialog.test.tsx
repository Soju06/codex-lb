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

    // Snapshot loads the soonest credit title.
    expect(await screen.findByText("Banked rate-limit reset")).toBeInTheDocument();

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

    expect(await screen.findByText("Banked rate-limit reset")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Redeem credit" }));

    await vi.waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("No reset credit available"),
    );
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
    // Failure leaves the dialog open for retry.
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});

