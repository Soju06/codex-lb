import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DonutChart } from "@/components/donut-chart";

const BASE_ITEMS = [
  { label: "Account A", value: 120, color: "#7bb661" },
  { label: "Account B", value: 80, color: "#d9a441" },
];

let scrollIntoViewMock: ReturnType<typeof vi.fn>;

describe("DonutChart", () => {
  beforeEach(() => {
    scrollIntoViewMock = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoViewMock,
    });
  });

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
    expect(screen.getByTestId("donut-caption")).toHaveTextContent("Total 200 · 0% used");
    expect(screen.getByTestId("donut-used-row")).toHaveTextContent("Used0");

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

  it("does not render a consumed segment when total equals sum of items", () => {
    const items = [
      { label: "Account A", value: 120, color: "#7bb661" },
      { label: "Account B", value: 80, color: "#d9a441" },
    ];
    const { container } = render(
      <DonutChart title="No Consumed" total={200} items={items} />,
    );

    // When total = sum(items), there should be exactly 2 cells (no gray consumed cell)
    const cells = container.querySelectorAll(".recharts-pie-sector");
    expect(cells).toHaveLength(2);
  });

  it("renders consumed gray segment when total exceeds sum of items", () => {
    const items = [
      { label: "Account A", value: 120, color: "#7bb661" },
      { label: "Account B", value: 80, color: "#d9a441" },
    ];
    const { container } = render(
      <DonutChart title="With Consumed" total={500} items={items} />,
    );

    // When total > sum(items), there should be 3 cells (2 items + 1 consumed gray)
    const cells = container.querySelectorAll(".recharts-pie-sector");
    expect(cells).toHaveLength(3);
    expect(screen.getByTestId("donut-caption")).toHaveTextContent("Total 500 · 60% used");
    expect(screen.getByTestId("donut-used-value")).toHaveTextContent("300");
  });

  it("can show remaining in the center while consumed uses full capacity", () => {
    render(
      <DonutChart
        title="Remaining vs Consumed"
        total={500}
        centerValue={200}
        items={BASE_ITEMS}
      />,
    );

    expect(screen.getByText("200")).toBeInTheDocument();
    expect(screen.getByTestId("donut-caption")).toHaveTextContent("Total 500 · 60% used");
  });

  it("renders a gray Used row beneath the account legend", () => {
    render(
      <DonutChart
        title="Used Legend"
        total={500}
        items={BASE_ITEMS}
      />,
    );

    expect(screen.getByText(/^Used$/)).toBeInTheDocument();
    expect(screen.getByTestId("donut-used-value")).toHaveTextContent("300");
  });

  it("highlights the matching legend row when a legend item is hovered", () => {
    render(
      <DonutChart
        title="Legend Hover"
        total={500}
        items={BASE_ITEMS}
      />,
    );

    const legendRow = screen.getByTestId("donut-legend-0");
    fireEvent.mouseEnter(legendRow);
    expect(legendRow).toHaveAttribute("data-active", "true");

    fireEvent.mouseLeave(legendRow);
    expect(legendRow).toHaveAttribute("data-active", "false");
  });

  it("highlights the matching legend row when a pie slice is hovered", () => {
    const { container } = render(
      <DonutChart
        title="Slice Hover"
        total={500}
        items={BASE_ITEMS}
      />,
    );

    const sectors = container.querySelectorAll(".recharts-pie-sector");
    const legendRow = screen.getByTestId("donut-legend-0");

    fireEvent.mouseEnter(sectors[0]!);
    expect(legendRow).toHaveAttribute("data-active", "true");
  });

  it("limits the legend list to four visible rows before scrolling", () => {
    render(
      <DonutChart
        title="Many Legends"
        total={1000}
        items={Array.from({ length: 5 }, (_, index) => ({
          label: `Account ${index + 1}`,
          value: 100,
          color: `#00000${index}`,
        }))}
      />,
    );

    expect(screen.getByTestId("donut-legend-list")).toHaveStyle({
      maxHeight: "calc(4 * 1.75rem + 1.875rem)",
    });
  });

  it("scrolls the hovered pie item into view in the legend list", async () => {
    const items = Array.from({ length: 5 }, (_, index) => ({
      label: `Account ${index + 1}`,
      value: 100,
      color: `#12345${index}`,
    }));
    const { container } = render(
      <DonutChart title="Scrollable Legends" total={1000} items={items} />,
    );

    const sectors = container.querySelectorAll(".recharts-pie-sector");
    const lastLegendRow = screen.getByTestId("donut-legend-4");

    fireEvent.mouseEnter(sectors[4]!);

    await waitFor(() => {
      expect(lastLegendRow).toHaveAttribute("data-active", "true");
      expect(scrollIntoViewMock).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
    });
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
