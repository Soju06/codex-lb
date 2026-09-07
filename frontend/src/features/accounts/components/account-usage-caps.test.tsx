import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { AccountUsageCaps } from "@/features/accounts/components/account-usage-caps";
import { AccountUsagePanel } from "@/features/accounts/components/account-usage-panel";
import { AccountListItem } from "@/features/accounts/components/account-list-item";
import { AccountCard } from "@/features/dashboard/components/account-card";
import { AccountList } from "@/features/dashboard/components/account-list";
import { createAccountSummary } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";

function setup(disabled = false, weeklyOnly = false) {
  const account = createAccountSummary({
    windowMinutesPrimary: weeklyOnly ? null : 300, windowMinutesSecondary: 10080,
    usageCap5HPercent: null, usageCapWeeklyPercent: 50,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(<QueryClientProvider client={client}><AccountUsageCaps account={account} disabled={disabled} /></QueryClientProvider>);
  return { account, invalidate };
}

describe("Usage caps", () => {
  it("saves independent caps and invalidates account and dashboard queries", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    server.use(http.put("/api/accounts/:accountId/usage-caps", async ({ request }) => {
      const body = await request.json();
      bodies.push(body);
      return HttpResponse.json(body);
    }));
    const { invalidate } = setup();
    await user.type(screen.getByRole("spinbutton", { name: "5h cap (% usable)" }), "80");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(bodies).toEqual([{ usageCap5HPercent: 80, usageCapWeeklyPercent: 50 }]));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    await user.clear(screen.getByRole("spinbutton", { name: "Weekly cap (% usable)" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(bodies[1]).toEqual({ usageCap5HPercent: 80, usageCapWeeklyPercent: null }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["accounts", "list"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dashboard", "overview"] });
  });

  it("rejects zero and over-100 caps", async () => {
    const user = userEvent.setup();
    setup();
    const input = screen.getByRole("spinbutton", { name: "5h cap (% usable)" });
    for (const value of ["0", "101"]) {
      await user.clear(input);
      await user.type(input, value);
      expect(screen.getByRole("alert")).toHaveTextContent("greater than 0");
      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    }
  });

  it("disables controls for read-only users", () => {
    setup(true);
    expect(screen.getByRole("spinbutton", { name: "Weekly cap (% usable)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("does not enable a nonexistent 5h window", () => {
    setup(false, true);
    expect(screen.getByRole("spinbutton", { name: "5h cap (% usable)" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: "Weekly cap (% usable)" })).toBeEnabled();
  });

  it("presents disable as an outlined full-width action", () => {
    setup();
    expect(screen.getByRole("button", { name: "Disable cap" })).toHaveAttribute("data-variant", "outline");
    expect(screen.getByRole("button", { name: "Disable cap" })).toHaveClass("w-full");
  });

  it.each(["detail", "account-list", "dashboard-card", "dashboard-list"])("segments reserved quota in %s", (surface) => {
    const account = createAccountSummary({
      usage: { primaryRemainingPercent: 46, secondaryRemainingPercent: 70 },
      usageCap5HPercent: 80,
      usageCapWeeklyPercent: 50,
    });
    if (surface === "detail") render(<AccountUsagePanel account={account} />);
    if (surface === "account-list") render(<AccountListItem account={account} selected={false} onSelect={vi.fn()} />);
    if (surface === "dashboard-card") render(<AccountCard account={account} />);
    if (surface === "dashboard-list") render(<AccountList accounts={[account]} />);
    expect(screen.getByRole("img", { name: "Cap: 80% used (20% remaining)" })).toHaveStyle({ width: "20%" });
    expect(screen.getByRole("img", { name: "Cap: 50% used (50% remaining)" })).toHaveStyle({ width: "50%" });
    const accountPageValues = surface === "detail";
    expect(screen.getByText(accountPageValues ? "46% (26% usable)" : "46% (26%)")).toBeInTheDocument();
    expect(screen.getByText(accountPageValues ? "70% (20% usable)" : "70% (20%)")).toBeInTheDocument();
  });
});
