import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountList } from "@/features/accounts/components/account-list";
import { createAccountSummary } from "@/test/mocks/factories";

const NOW = Date.parse("2026-10-04T12:00:00Z");
const DAY = 86_400_000;
const warning = "Reset credit expires within 3 days";
const actions = { selectedAccountId: null, onSelect: vi.fn(), onOpenImport: vi.fn(), onOpenOauth: vi.fn() };

function account(expiresAt: string | null, count = 2) {
  return { ...createAccountSummary({ availableResetCredits: count }), resetCreditNearestExpiresAt: expiresAt };
}

describe("Accounts list reset-credit expiry", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    [NOW + 3 * DAY, true],
    [NOW + 3 * DAY + 1, false],
    [NOW + 1, true],
    [NOW, false],
    [NOW - 1, false],
    [null, false],
    ["invalid", false],
  ])("marks only future expiries within 72 hours: %s", (expiry, visible) => {
    const value = typeof expiry === "number" ? new Date(expiry).toISOString() : expiry;
    render(<AccountList {...actions} accounts={[account(value)]} />);
    expect(screen.queryByRole("img", { name: warning }) !== null).toBe(visible);
  });

  it.each([
    { count: 0, showResetCreditBadges: true, showResetCreditExpiryBadge: true },
    { count: 2, showResetCreditBadges: false, showResetCreditExpiryBadge: true },
    { count: 2, showResetCreditBadges: true, showResetCreditExpiryBadge: false },
  ])("respects count and visibility: %j", ({ count, ...visibility }) => {
    render(<AccountList {...actions} {...visibility} accounts={[account(new Date(NOW + DAY).toISOString(), count)]} />);
    expect(screen.queryByRole("img", { name: warning })).not.toBeInTheDocument();
  });

  it("enters the warning window without a refetch and cleans up its timer", () => {
    const view = render(<AccountList {...actions} accounts={[account(new Date(NOW + 3 * DAY + 30_000).toISOString())]} />);
    expect(screen.queryByRole("img", { name: warning })).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByRole("img", { name: warning })).toBeInTheDocument();
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("removes the warning after expiry and responds to refreshed credit data", () => {
    const view = render(<AccountList {...actions} accounts={[account(new Date(NOW + 30_000).toISOString())]} />);
    expect(screen.getByRole("img", { name: warning })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.queryByRole("img", { name: warning })).not.toBeInTheDocument();
    view.rerender(<AccountList {...actions} accounts={[account(new Date(NOW + DAY).toISOString())]} />);
    expect(screen.getByRole("img", { name: warning })).toBeInTheDocument();
    view.rerender(<AccountList {...actions} accounts={[account(new Date(NOW + DAY).toISOString(), 0)]} />);
    expect(screen.queryByRole("img", { name: warning })).not.toBeInTheDocument();
  });
});
