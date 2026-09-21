import { HttpResponse, http } from "msw";
import { useState, type ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResetCreditConfirmDialog } from "@/features/accounts/components/reset-credit-confirm-dialog";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import { createAccountSummary } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { formatDateTimeInline } from "@/utils/formatters";

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
    info: vi.fn(),
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
  it("retains unresolved request identity across closing and remounting the dialog", async () => {
    const user = userEvent.setup();
    const bodies: Array<{ redeemRequestId: string }> = [];
    server.use(
      http.get(SNAPSHOT_URL, snapshotResponse),
      http.post(CONSUME_URL, async ({ request }) => {
        bodies.push(await request.json() as { redeemRequestId: string });
        if (bodies.length === 1) {
          return HttpResponse.json({ error: { code: "temporary_upstream_error", message: "Temporary failure" } }, { status: 503 });
        }
        return HttpResponse.json({ code: "reset", outcome: "confirmed_reset", windowsReset: 1, redeemedAt: null });
      }),
    );
    function Harness() {
      const [open, setOpen] = useState(true);
      return <>
        <button onClick={() => setOpen(true)}>Open reset</button>
        {open && <ResetCreditConfirmDialog open onOpenChange={setOpen} accountId="acc_primary" />}
      </>;
    }
    renderWithClient(<Harness />);
    await screen.findByText("1 free rate limit reset");
    await user.click(screen.getByRole("button", { name: "Redeem credit" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "Redeem credit" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Open reset" }));
    await screen.findByText("1 free rate limit reset");
    await user.click(screen.getByRole("button", { name: "Redeem credit" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(bodies).toHaveLength(2);
    expect(bodies[1].redeemRequestId).toBe(bodies[0].redeemRequestId);
    // A confirmed receipt releases identity for the next intentional reset.
    await user.click(screen.getByRole("button", { name: "Open reset" }));
    await screen.findByText("1 free rate limit reset");
    await user.click(screen.getByRole("button", { name: "Redeem credit" }));
    await waitFor(() => expect(bodies).toHaveLength(3));
    expect(bodies[2].redeemRequestId).not.toBe(bodies[0].redeemRequestId);
  });

  beforeEach(() => {
    useDateDisplayFormatStore.setState({ dateDisplayFormat: "iso8601" });
    server.use(http.get("/api/accounts/acc_primary/summary", () => HttpResponse.json(createAccountSummary({ accountId: "acc_primary", resetCreditFetchedAt: "2026-09-21T00:00:00Z" }))));
  });

  it("confirms and consumes the soonest reset credit, then invalidates queries", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const consumeCalled = vi.fn();
    server.use(
      http.get(SNAPSHOT_URL, snapshotResponse),
      http.post(CONSUME_URL, () => {
        consumeCalled();
        return HttpResponse.json({
          code: "reset",
          outcome: "confirmed_reset",
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
    const expiresAt = "2026-01-08T12:00:00.000Z";
    const isoExpiry = formatDateTimeInline(expiresAt, "iso8601");
    const expiryLine = screen.getByText((_, element) =>
      element?.tagName === "P" && (element.textContent ?? "").includes(`Reset expires on ${isoExpiry}`),
    );
    expect(expiryLine).toBeInTheDocument();

    act(() => {
      useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
    });

    expect(expiryLine).toHaveTextContent(formatDateTimeInline(expiresAt, "default"));
    expect(expiryLine).not.toHaveTextContent(isoExpiry);

    await user.click(screen.getByRole("button", { name: "Redeem credit" }));

    await vi.waitFor(() => expect(consumeCalled).toHaveBeenCalledTimes(1));
    await vi.waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Rate-limit window reset (1)"),
    );
    await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["accounts", "trends", "acc_primary"], exact: true });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["dashboard", "projections"] });
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

  it("reuses one redeem request id when retrying while the dialog stays open", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const bodies: unknown[] = [];
    server.use(
      http.get(SNAPSHOT_URL, snapshotResponse),
      http.post(CONSUME_URL, async ({ request }) => {
        bodies.push(await request.json());
        if (bodies.length === 1) {
          return HttpResponse.json(
            {
              error: {
                code: "temporary_upstream_error",
                message: "Upstream response was lost",
              },
            },
            { status: 502 },
          );
        }
        return HttpResponse.json({
          code: "reset",
          outcome: "confirmed_reset",
          windowsReset: 1,
          redeemedAt: "2026-01-01T12:00:00.000Z",
        });
      }),
    );

    renderWithClient(
      <ResetCreditConfirmDialog
        open
        onOpenChange={onOpenChange}
        accountId="acc_primary"
      />,
    );

    expect(await screen.findByText("1 free rate limit reset")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Redeem credit" }));
    await vi.waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Upstream response was lost"),
    );
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    await user.click(screen.getByRole("button", { name: "Redeem credit" }));
    await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));

    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toEqual({ redeemRequestId: expect.any(String) });
    expect(bodies[1]).toEqual(bodies[0]);
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
          code: "reset",
          outcome: "confirmed_reset",
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
