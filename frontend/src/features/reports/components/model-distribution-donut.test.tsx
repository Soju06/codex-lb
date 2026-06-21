import type { ReactNode } from "react";
import userEvent from "@testing-library/user-event";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelDistributionDonut } from "./model-distribution-donut";

vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();

  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => (
      <div data-testid="responsive-container">{children}</div>
    ),
    PieChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    Pie: ({
      data,
      dataKey,
      onMouseEnter,
      onMouseLeave,
      shape,
    }: {
      data: Array<{ model: string }>;
      dataKey: string;
      onMouseEnter?: (entry: { model: string }, index: number) => void;
      onMouseLeave?: (entry: { model: string }, index: number) => void;
      shape?: unknown;
    }) => (
      <div
        data-testid="model-distribution-pie"
        data-key={dataKey}
        data-shape={shape ? "true" : "false"}
      >
        {data.map((entry, index) => (
          <button
            key={entry.model}
            type="button"
            data-testid={`model-slice-${index}`}
            onMouseEnter={() => onMouseEnter?.(entry, index)}
            onMouseLeave={() => onMouseLeave?.(entry, index)}
          >
            {entry.model}
          </button>
        ))}
      </div>
    ),
    Cell: () => null,
  };
});

describe("ModelDistributionDonut", () => {
  it("highlights the matching legend row when a legend item is hovered", () => {
    render(
      <ModelDistributionDonut
        data={[
          { model: "gpt-5", costUsd: 42.02, requests: 2, percentage: 70 },
          { model: "o3", costUsd: 18.03, requests: 8, percentage: 30 },
        ]}
      />,
    );

    const legendRow = screen.getByTestId("model-distribution-legend-0");

    expect(screen.getByTestId("model-distribution-pie")).toHaveAttribute("data-shape", "true");
    expect(legendRow).toHaveAttribute("data-active", "false");

    fireEvent.mouseEnter(legendRow);
    expect(legendRow).toHaveAttribute("data-active", "true");

    fireEvent.mouseLeave(legendRow);
    expect(legendRow).toHaveAttribute("data-active", "false");
  });

  it("highlights the matching legend row when a pie slice is hovered", () => {
    render(
      <ModelDistributionDonut
        data={[
          { model: "gpt-5", costUsd: 42.02, requests: 2, percentage: 70 },
          { model: "o3", costUsd: 18.03, requests: 8, percentage: 30 },
        ]}
      />,
    );

    const slice = screen.getByTestId("model-slice-0");
    const legendRow = screen.getByTestId("model-distribution-legend-0");

    fireEvent.mouseEnter(slice);
    expect(legendRow).toHaveAttribute("data-active", "true");
  });

  it("limits the legend viewport to four visible rows before scrolling", () => {
    render(
      <ModelDistributionDonut
        data={Array.from({ length: 5 }, (_, index) => ({
          model: `model-${index + 1}`,
          costUsd: index + 1,
          requests: index + 1,
          percentage: 20,
        }))}
      />,
    );

    expect(screen.getByTestId("model-distribution-legend-list")).toHaveStyle({
      maxHeight: "calc(4 * 2rem)",
    });
    expect(screen.getByTestId("model-distribution-legend-4")).toBeInTheDocument();
  });

  it("shows the total label and compact cost total in the donut center by default", () => {
    render(
      <ModelDistributionDonut
        data={[
          { model: "gpt-5", costUsd: 430, requests: 2, percentage: 30 },
          { model: "gpt-5-pro", costUsd: 1000, requests: 10, percentage: 70 },
        ]}
      />,
    );

    expect(screen.getByTestId("model-distribution-center-label")).toHaveTextContent("Total");
    expect(screen.getByTestId("model-distribution-center-value")).toHaveTextContent("$1.43K");
  });

  it("pads legend value cells to the longest formatted cost", () => {
    render(
      <ModelDistributionDonut
        data={[
          { model: "gpt-5", costUsd: 42.02, requests: 2, percentage: 24.0 },
          { model: "gpt-5-pro", costUsd: 128.55, requests: 10, percentage: 76.0 },
        ]}
      />,
    );

    const smallCostLegendValue = screen.getAllByText("$42.02").at(-1);
    const largeCostLegendValue = screen.getAllByText("$128.55").at(-1);

    expect(smallCostLegendValue).toBeDefined();
    expect(largeCostLegendValue).toBeDefined();
    expect(smallCostLegendValue).toHaveStyle({ minWidth: "7ch" });
    expect(largeCostLegendValue).toHaveStyle({ minWidth: "7ch" });
  });

  it("defaults to cost mode without rendering a donut tooltip", () => {
    render(
      <ModelDistributionDonut
        data={[
          { model: "gpt-5", costUsd: 42.02, requests: 2, percentage: 70 },
          { model: "o3", costUsd: 18.03, requests: 8, percentage: 30 },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: /^cost$/i })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^req$/i })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("model-distribution-center-value")).toHaveTextContent("$60.05");
    expect(screen.getByText("$18.03")).toBeInTheDocument();
    expect(screen.getByTestId("model-distribution-pie")).toHaveAttribute("data-key", "costUsd");
    expect(screen.queryByText(/^Cost$/)).not.toBeInTheDocument();
  });

  it("switches to request mode for slices, legend values, and percentages without rendering a donut tooltip", async () => {
    const user = userEvent.setup();

    render(
      <ModelDistributionDonut
        data={[
          { model: "gpt-5", costUsd: 42.02, requests: 2, percentage: 70 },
          { model: "o3", costUsd: 18.03, requests: 8, percentage: 30 },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^req$/i }));

    expect(screen.getByText("20.0%")).toBeInTheDocument();
    expect(screen.getByText("80.0%")).toBeInTheDocument();
    expect(screen.getByText(/^8$/)).toBeInTheDocument();
    expect(screen.getByTestId("model-distribution-center-value")).toHaveTextContent("10");
    expect(screen.getByTestId("model-distribution-pie")).toHaveAttribute("data-key", "requests");
    expect(screen.getByRole("button", { name: /^cost$/i })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: /^req$/i })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/^Requests$/)).not.toBeInTheDocument();
  });

  it("uses compact request totals in the center and legend when request mode is active", async () => {
    const user = userEvent.setup();

    render(
      <ModelDistributionDonut
        data={[
          { model: "gpt-5", costUsd: 42.02, requests: 500_000_000, percentage: 40 },
          { model: "o3", costUsd: 18.03, requests: 1_000_000_000, percentage: 60 },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^req$/i }));

    expect(screen.getByTestId("model-distribution-center-value")).toHaveTextContent("1.5B");
    expect(screen.getByText("500M")).toBeInTheDocument();
    expect(screen.getByText("1B")).toBeInTheDocument();
  });

  it("scrolls the hovered pie item into view in the legend list", () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    render(
      <ModelDistributionDonut
        data={Array.from({ length: 5 }, (_, index) => ({
          model: `model-${index + 1}`,
          costUsd: 5 - index,
          requests: index + 1,
          percentage: 20,
        }))}
      />,
    );

    fireEvent.mouseEnter(screen.getByTestId("model-slice-4"));

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
  });
});
