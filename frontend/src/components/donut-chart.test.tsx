import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DonutChart } from "@/components/donut-chart";

describe("DonutChart", () => {
  it("renders chart title, subtitle, legend, and SVG", () => {
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

    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
  });

  it("renders consumed segment when items sum is less than total", () => {
    const { container } = render(
      <DonutChart
        title="Test"
        total={100}
        items={[{ label: "A", value: 40, color: "#111111" }]}
      />,
    );

    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("renders empty state when total is zero", () => {
    const { container } = render(
      <DonutChart title="Empty" total={0} items={[]} />,
    );

    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(screen.getByText("Remaining")).toBeInTheDocument();
  });
});
