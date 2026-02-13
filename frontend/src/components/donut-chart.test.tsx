import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DonutChart } from "@/components/donut-chart";

describe("DonutChart", () => {
  it("renders chart title, legend, and conic gradient", () => {
    const { container } = render(
      <DonutChart
        title="Primary Remaining"
        subtitle="Window 5h"
        total={200}
        items={[
          { label: "Account A", value: 120, color: "#7bb661" },
          { label: "Account B", value: 80, color: "#d9a441" },
        ]}
      />,
    );

    expect(screen.getByText("Primary Remaining")).toBeInTheDocument();
    expect(screen.getByText("Window 5h")).toBeInTheDocument();
    expect(screen.getByText("Account A")).toBeInTheDocument();
    expect(screen.getByText("Account B")).toBeInTheDocument();
    expect(screen.getByText("Remaining")).toBeInTheDocument();

    const gradientNode = container.querySelector('div[style*="conic-gradient"]');
    expect(gradientNode).not.toBeNull();
  });
});
