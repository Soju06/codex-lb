import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UsageDonuts } from "@/features/dashboard/components/usage-donuts";

describe("UsageDonuts", () => {
  it("renders primary and secondary donut panels with legends", () => {
    render(
      <UsageDonuts
        primaryItems={[
          {
            accountId: "acc-1",
            label: "primary@example.com",
            value: 120,
            remainingPercent: 60,
            color: "#7bb661",
          },
        ]}
        secondaryItems={[
          {
            accountId: "acc-2",
            label: "secondary@example.com",
            value: 80,
            remainingPercent: 40,
            color: "#d9a441",
          },
        ]}
        primaryTotal={200}
        secondaryTotal={200}
        primaryWindowMinutes={300}
        secondaryWindowMinutes={10080}
      />,
    );

    expect(screen.getByText("Primary Remaining")).toBeInTheDocument();
    expect(screen.getByText("Secondary Remaining")).toBeInTheDocument();
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
        primaryWindowMinutes={null}
        secondaryWindowMinutes={null}
      />,
    );

    expect(screen.getByText("Primary Remaining")).toBeInTheDocument();
    expect(screen.getByText("Secondary Remaining")).toBeInTheDocument();
    expect(screen.getAllByText("Remaining").length).toBeGreaterThanOrEqual(2);
  });
});
