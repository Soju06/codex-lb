import { HttpResponse, http } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RedeemAllResetCreditsDialog } from "@/features/accounts/components/redeem-all-reset-credits-dialog";
import type { RedeemAllTarget } from "@/features/accounts/redeem-all";
import { server } from "@/test/mocks/server";

const { toastSuccess, toastError, toastWarning } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  toastWarning: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, warning: toastWarning },
}));

const TARGETS: RedeemAllTarget[] = [
  { accountId: "acc_a", label: "Alpha", email: "a@example.com", availableResetCredits: 2 },
  { accountId: "acc_b", label: "Beta", email: "b@example.com", availableResetCredits: 1 },
];

const consumeUrl = (accountId: string) => `/api/accounts/${accountId}/rate-limit-reset-credits/consume`;

function renderDialog(onOpenChange = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RedeemAllResetCreditsDialog open onOpenChange={onOpenChange} targets={TARGETS} />
    </QueryClientProvider>,
  );
  return onOpenChange;
}

describe("RedeemAllResetCreditsDialog", () => {
  it("lists every account and credit count before anything is redeemed", () => {
    renderDialog();

    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("2 credits available")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("1 credit available")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redeem on 2 accounts" })).toBeEnabled();
  });

  it("redeems each account once with its own request id and closes when all succeed", async () => {
    const user = userEvent.setup();
    const seen: Array<{ accountId: string; body: unknown }> = [];
    for (const accountId of ["acc_a", "acc_b"]) {
      server.use(
        http.post(consumeUrl(accountId), async ({ request }) => {
          seen.push({ accountId, body: await request.json() });
          return HttpResponse.json({ code: "reset", windowsReset: 1, redeemedAt: "2026-01-01T12:00:00.000Z" });
        }),
      );
    }
    const onOpenChange = renderDialog();

    await user.click(screen.getByRole("button", { name: "Redeem on 2 accounts" }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(seen.map((call) => call.accountId)).toEqual(["acc_a", "acc_b"]);
    const requestIds = seen.map((call) => (call.body as { redeemRequestId: string }).redeemRequestId);
    expect(requestIds.every((id) => typeof id === "string" && id.length > 0)).toBe(true);
    expect(new Set(requestIds).size).toBe(2);
    expect(toastSuccess).toHaveBeenCalledWith("Redeemed a reset credit on 2 accounts");
  });

  it("keeps going after a failure and reports per-account results", async () => {
    const user = userEvent.setup();
    const betaCalled = vi.fn();
    server.use(
      http.post(consumeUrl("acc_a"), () =>
        HttpResponse.json(
          { error: { code: "no_available_reset_credit", message: "No available reset credit" } },
          { status: 409 },
        ),
      ),
      http.post(consumeUrl("acc_b"), () => {
        betaCalled();
        return HttpResponse.json({ code: "reset", windowsReset: 1, redeemedAt: "2026-01-01T12:00:00.000Z" });
      }),
    );
    const onOpenChange = renderDialog();

    await user.click(screen.getByRole("button", { name: "Redeem on 2 accounts" }));

    expect(await screen.findByText("No available reset credit")).toBeInTheDocument();
    expect(betaCalled).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Redeemed")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(toastWarning).toHaveBeenCalledWith("Redeemed on 1 of 2 accounts; 1 failed");

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
