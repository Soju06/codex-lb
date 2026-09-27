import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountListOverviewRow } from "./account-list-overview-row";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { createAccountSummary } from "@/test/mocks/factories";

const now = "2026-09-27T12:00:00Z";

describe("AccountListOverviewRow", () => {
  beforeEach(() => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse(now));
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "both" });
    act(() => usePrivacyStore.setState({ blurred: false }));
  });
  afterEach(() => {
    vi.restoreAllMocks();
    act(() => usePrivacyStore.setState({ blurred: false }));
  });

  it("shows the compact overview and opens details with the keyboard", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <AccountListOverviewRow
        selected={false}
        onSelect={onSelect}
        account={createAccountSummary({
          alias: "Personal Plus",
          displayName: "Personal Plus",
          workspaceLabel: "Product",
          seatType: "owner",
          subscription: {
            activeUntil: "2026-10-02T12:00:00Z",
            lastCheckedAt: now,
          },
          requestUsage: {
            requestCount: 24,
            totalTokens: 15000,
            cachedInputTokens: 5000,
            totalCostUsd: 1.25,
          },
          auth: {
            access: { expiresAt: "2026-09-27T13:00:00Z" },
            refresh: { state: "stored" },
            idToken: { state: "parsed" },
          },
          lastRefreshAt: now,
          availableResetCredits: 3,
          limitWarmupEnabled: true,
          routingPolicy: "burn_first",
          additionalQuotas: [
            {
              quotaKey: "review",
              limitName: "Review",
              meteredFeature: "review",
              primaryWindow: {
                usedPercent: 20,
                resetAt: Date.parse(now) / 1000 + 3600,
                windowMinutes: 300,
              },
            },
          ],
        })}
      />,
    );
    for (const label of [
      "Personal Plus",
      "primary@example.com",
      "Product · Owner",
      "Plus",
      "Active",
      "Reset (3)",
      "05d 00h",
      "5h",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByTestId("account-plan-remaining")).toHaveAttribute(
      "title",
      expect.stringContaining("Recorded end date:"),
    );
    expect(screen.getByTestId("account-plan-remaining")).toHaveAttribute(
      "title",
      expect.stringContaining("Last checked:"),
    );
    expect(screen.queryByText(/Recorded end date:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Last checked:/)).not.toBeInTheDocument();
    expect(screen.getByTestId("list-overview-quota-5h")).toBeInTheDocument();
    expect(screen.queryByText(/requests/)).not.toBeInTheDocument();
    for (const label of [
      "Stored",
      "Parsed",
      "Token refreshed",
      "Warm-up on",
      "$1.25",
      "Credits: 932",
      "3",
    ]) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
    expect(screen.queryByText("Burn first")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("list-overview-quota-Weekly"),
    ).toBeInTheDocument();
    await user.tab();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("acc_primary");
  });

  it("masks the email subtitle without masking an alias", () => {
    usePrivacyStore.setState({ blurred: true });
    render(
      <AccountListOverviewRow
        selected={false}
        onSelect={() => {}}
        account={createAccountSummary({
          alias: "Private account",
          displayName: "Private account",
        })}
      />,
    );
    expect(screen.getByText("Private account")).not.toHaveClass("privacy-blur");
    expect(screen.getByText("primary@example.com")).toHaveClass("privacy-blur");
  });

  it("masks an email title and shows it only once", () => {
    usePrivacyStore.setState({ blurred: true });
    render(
      <AccountListOverviewRow
        selected={false}
        onSelect={() => {}}
        account={createAccountSummary()}
      />,
    );
    expect(screen.getAllByText("primary@example.com")).toHaveLength(1);
    expect(screen.getByText("primary@example.com")).toHaveClass("privacy-blur");
  });

  it("retains monthly-only quota under a weekly preference and does not infer a subscription", () => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "weekly" });
    render(
      <AccountListOverviewRow
        selected={false}
        onSelect={() => {}}
        account={createAccountSummary({
          windowMinutesPrimary: null,
          windowMinutesSecondary: null,
          resetAtPrimary: null,
          resetAtSecondary: null,
          windowMinutesMonthly: 43200,
          usage: {
            primaryRemainingPercent: null,
            secondaryRemainingPercent: null,
            monthlyRemainingPercent: 12.4,
          },
          subscription: null,
        })}
      />,
    );
    expect(screen.getByText("Monthly")).toBeInTheDocument();
    expect(screen.getByText("12.4%")).toBeInTheDocument();
    expect(screen.queryByText("Weekly")).not.toBeInTheDocument();
    expect(screen.getByText("No data")).toBeInTheDocument();
    expect(screen.queryByText(/Recorded end date:/)).not.toBeInTheDocument();
  });

  it("preserves recovery status with its reason in a tooltip", () => {
    render(
      <AccountListOverviewRow
        selected={false}
        onSelect={() => {}}
        account={createAccountSummary({
          status: "reauth_required",
          availableResetCredits: 3,
          routingPolicy: "burn_first",
          deactivationReason: "Session requires re-authentication",
        })}
      />,
    );
    expect(screen.getByText("Re-auth required")).toBeInTheDocument();
    expect(screen.getByText("Re-auth required")).toHaveAttribute(
      "title",
      "Session requires re-authentication",
    );
    expect(screen.queryByText("Burn first")).not.toBeInTheDocument();
    expect(screen.getByText("Reset (3)")).toBeInTheDocument();
  });

  it.each([0, null, undefined])(
    "omits reset counts when none are available (%s)",
    (count) => {
      render(
        <AccountListOverviewRow
          selected={false}
          onSelect={() => {}}
          account={createAccountSummary({ availableResetCredits: count })}
        />,
      );
      expect(screen.queryByText(/^Reset \(/)).not.toBeInTheDocument();
    },
  );
});
