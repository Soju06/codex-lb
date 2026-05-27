import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { UsageDonuts } from "@/features/dashboard/components/usage-donuts";

/** Helper to build a minimal RemainingItem for tests. */
function item(overrides: { accountId: string; label: string; value: number; remainingPercent: number; color: string; status?: string }) {
  return { status: "active", ...overrides, labelSuffix: "", isEmail: true };
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

    expect(screen.getByText("5-Hour Credits")).toBeInTheDocument();
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

    expect(screen.getByText("5-Hour Credits")).toBeInTheDocument();
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

  it("shows remaining credits and capacity as stacked values with a divider in the center", () => {
    // Regression for #371 + redesigned display: dashboard donuts previously
    // showed compact-formatted numbers like "7.33k" / "7.56k". Operators
    // asked for the raw remaining/total credit counts instead so the
    // exact distance to the cap is visible at a glance. Now split into
    // stacked rows: remaining on top, capacity below a divider.
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

    const remaining = screen.getAllByTestId("donut-center-remaining").map((node) => node.textContent);
    const capacity = screen.getAllByTestId("donut-center-capacity").map((node) => node.textContent);
    expect(remaining).toEqual(["120", "7,331"]);
    expect(capacity).toEqual(["225", "7,560"]);
  });

  it("hides deactivated account slices by default and allows choosing multiple statuses", async () => {
    const user = userEvent.setup();
    render(
      <UsageDonuts
        primaryItems={[
          item({ accountId: "acc-active", label: "active@example.com", value: 120, remainingPercent: 60, color: "#7bb661" }),
          item({ accountId: "acc-paused", label: "paused@example.com", value: 80, remainingPercent: 40, color: "#d9a441", status: "paused" }),
          item({ accountId: "acc-deactivated", label: "inactive@example.com", value: 30, remainingPercent: 30, color: "#d14c7a", status: "deactivated" }),
        ]}
        secondaryItems={[
          item({ accountId: "acc-active", label: "active@example.com", value: 120, remainingPercent: 60, color: "#7bb661" }),
          item({ accountId: "acc-paused", label: "paused@example.com", value: 80, remainingPercent: 40, color: "#d9a441", status: "paused" }),
          item({ accountId: "acc-deactivated", label: "inactive@example.com", value: 30, remainingPercent: 30, color: "#d14c7a", status: "deactivated" }),
        ]}
        primaryTotal={400}
        secondaryTotal={400}
      />,
    );

    expect(screen.getAllByText("active@example.com")).toHaveLength(2);
    expect(screen.getAllByText("paused@example.com")).toHaveLength(2);
    expect(screen.queryByText("inactive@example.com")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /statuses/i }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: /deactivated/i }));

    expect(screen.getAllByText("active@example.com")).toHaveLength(2);
    expect(screen.getAllByText("paused@example.com")).toHaveLength(2);
    expect(screen.getAllByText("inactive@example.com")).toHaveLength(2);
  });
});
