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

    expect(screen.getByText("5h Remaining")).toBeInTheDocument();
    expect(screen.getByText("Weekly Remaining")).toBeInTheDocument();
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

    expect(screen.getByText("5h Remaining")).toBeInTheDocument();
    expect(screen.getByText("Weekly Remaining")).toBeInTheDocument();
    expect(screen.getAllByText("Remaining").length).toBeGreaterThanOrEqual(2);
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

  it("shows remaining totals in the center while donut totals can use capacity", () => {
    const { container } = render(
      <UsageDonuts
        primaryItems={[item({ accountId: "acc-1", label: "primary@example.com", value: 120, remainingPercent: 60, color: "#7bb661" })]}
        secondaryItems={[item({ accountId: "acc-2", label: "secondary@example.com", value: 80, remainingPercent: 40, color: "#d9a441" })]}
        primaryTotal={225}
        secondaryTotal={7560}
        primaryCenterValue={120}
        secondaryCenterValue={80}
      />,
    );

    const centerValues = Array.from(container.querySelectorAll(".text-base.font-semibold.tabular-nums")).map((node) => node.textContent);
    expect(centerValues).toEqual(["120", "80"]);
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
