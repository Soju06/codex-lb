import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UsageDonuts } from "@/features/dashboard/components/usage-donuts";

/** Helper to build a minimal RemainingItem for tests. */
function item(overrides: { accountId: string; label: string; value: number; remainingPercent: number; color: string }) {
  return { ...overrides, labelSuffix: "", isEmail: true };
}

describe("UsageDonuts", () => {
  it("renders primary and secondary donut panels with legends", () => {
    render(
      <UsageDonuts
        primaryItems={[item({ accountId: "acc-1", label: "primary@example.com", value: 120, remainingPercent: 60, color: "#7bb661" })]}
        secondaryItems={[item({ accountId: "acc-2", label: "secondary@example.com", value: 80, remainingPercent: 40, color: "#d9a441" })]}
        primaryTotal={200}
        secondaryTotal={200}
      />,
    );

    expect(screen.getByText("Hourly Credits")).toBeInTheDocument();
    expect(screen.getByText("Weekly Credits")).toBeInTheDocument();
    expect(screen.getByText("primary@example.com")).toBeInTheDocument();
    expect(screen.getByText("secondary@example.com")).toBeInTheDocument();
  });

  it("handles empty data gracefully", () => {
    render(
      <UsageDonuts
        primaryItems={[]}
        secondaryItems={[]}
        primaryTotal={0}
        secondaryTotal={0}
      />,
    );

    expect(screen.getByText("Hourly Credits")).toBeInTheDocument();
    expect(screen.getByText("Weekly Credits")).toBeInTheDocument();
    // Center label switched from "Remaining" -> "Credits" with the
    // credits layout; assert that both donuts render the new label.
    expect(screen.getAllByText("Credits").length).toBeGreaterThanOrEqual(2);
  });

  it("renders safe line only for the primary donut", () => {
    render(
      <UsageDonuts
        primaryItems={[item({ accountId: "acc-1", label: "primary@example.com", value: 120, remainingPercent: 60, color: "#7bb661" })]}
        secondaryItems={[item({ accountId: "acc-2", label: "secondary@example.com", value: 80, remainingPercent: 40, color: "#d9a441" })]}
        primaryTotal={200}
        secondaryTotal={200}
        safeLinePrimary={{ safePercent: 60, riskLevel: "warning" }}
      />,
    );

    expect(screen.getAllByTestId("safe-line-tick")).toHaveLength(1);
  });

  it("renders safe line on both donuts when both have depletion", () => {
    render(
      <UsageDonuts
        primaryItems={[item({ accountId: "acc-1", label: "primary@example.com", value: 120, remainingPercent: 60, color: "#7bb661" })]}
        secondaryItems={[item({ accountId: "acc-2", label: "secondary@example.com", value: 80, remainingPercent: 40, color: "#d9a441" })]}
        primaryTotal={200}
        secondaryTotal={200}
        safeLinePrimary={{ safePercent: 60, riskLevel: "warning" }}
        safeLineSecondary={{ safePercent: 40, riskLevel: "danger" }}
      />,
    );

    expect(screen.getAllByTestId("safe-line-tick")).toHaveLength(2);
  });

  it("renders safe line only on secondary donut for weekly-only plans", () => {
    render(
      <UsageDonuts
        primaryItems={[]}
        secondaryItems={[item({ accountId: "acc-1", label: "weekly@example.com", value: 80, remainingPercent: 40, color: "#d9a441" })]}
        primaryTotal={0}
        secondaryTotal={200}
        safeLineSecondary={{ safePercent: 60, riskLevel: "warning" }}
      />,
    );

    expect(screen.getAllByTestId("safe-line-tick")).toHaveLength(1);
  });

  it("shows remaining credits as raw remaining/total fraction in the center", () => {
    // Regression for #371: dashboard donuts previously showed
    // compact-formatted numbers like "7.33k" / "7.56k". Operators
    // asked for the raw remaining/total credit counts instead so the
    // exact distance to the cap is visible at a glance.
    render(
      <UsageDonuts
        primaryItems={[item({ accountId: "acc-1", label: "primary@example.com", value: 120, remainingPercent: 60, color: "#7bb661" })]}
        secondaryItems={[item({ accountId: "acc-2", label: "secondary@example.com", value: 7331, remainingPercent: 97, color: "#d9a441" })]}
        primaryTotal={225}
        secondaryTotal={7560}
        primaryCenterValue={120}
        secondaryCenterValue={7331}
      />,
    );

    const fractions = screen.getAllByTestId("donut-center-fraction").map((node) => node.textContent);
    expect(fractions).toEqual([
      // Primary: 120 / 225 (well under 4-digit thresholds; no separators).
      "120/225",
      // Secondary: 7,331 / 7,560 — locale-formatted, NOT "7.33k/7.56k".
      "7,331/7,560",
    ]);
  });
});
