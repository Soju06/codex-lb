import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DonutChart } from "@/components/donut-chart";

const BASE_ITEMS = [
  { label: "Account A", value: 120, color: "#7bb661" },
  { label: "Account B", value: 80, color: "#d9a441" },
];

describe("DonutChart", () => {
  it("renders chart title, subtitle, legend, and SVG", () => {
    const { container } = render(
      <DonutChart
        title="Primary Remaining"
        subtitle="Window 5h"
        total={200}
        items={BASE_ITEMS}
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

  it("renders without safeLine (no regression)", () => {
    render(<DonutChart title="No Line" total={200} items={BASE_ITEMS} />);

    expect(screen.queryByTestId("safe-line-tick")).toBeNull();
  });

  it("renders no tick mark when safeLine is null", () => {
    render(<DonutChart title="Null Line" total={200} items={BASE_ITEMS} safeLine={null} />);

    expect(screen.queryByTestId("safe-line-tick")).toBeNull();
  });

  it("renders no tick mark when riskLevel is safe", () => {
    render(
      <DonutChart
        title="Safe"
        total={200}
        items={BASE_ITEMS}
        safeLine={{ safePercent: 60, riskLevel: "safe" }}
      />,
    );

    expect(screen.queryByTestId("safe-line-tick")).toBeNull();
  });

  it("renders a <line> tick mark for warning riskLevel", () => {
    render(
      <DonutChart
        title="Warning"
        total={200}
        items={BASE_ITEMS}
        safeLine={{ safePercent: 60, riskLevel: "warning" }}
      />,
    );

    const tick = screen.getByTestId("safe-line-tick");
    expect(tick).toBeInTheDocument();
    expect(tick.tagName.toLowerCase()).toBe("line");
    expect(tick.getAttribute("stroke")).toBeTruthy();
  });

  it("renders tick mark for danger riskLevel", () => {
    render(
      <DonutChart
        title="Danger"
        total={200}
        items={BASE_ITEMS}
        safeLine={{ safePercent: 80, riskLevel: "danger" }}
      />,
    );

    expect(screen.getByTestId("safe-line-tick")).toBeInTheDocument();
  });

  it("renders tick mark for critical riskLevel", () => {
    render(
      <DonutChart
        title="Critical"
        total={200}
        items={BASE_ITEMS}
        safeLine={{ safePercent: 90, riskLevel: "critical" }}
      />,
    );

    expect(screen.getByTestId("safe-line-tick")).toBeInTheDocument();
  });
});
