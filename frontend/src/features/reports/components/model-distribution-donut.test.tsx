import { cloneElement, isValidElement, type ReactNode } from "react";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelDistributionDonut } from "./model-distribution-donut";

type MockTooltipContentProps = {
  names?: {
    requests?: unknown;
  };
};

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
    }: {
      data: Array<{ model: string }>;
      dataKey: string;
      onMouseEnter?: (entry: { model: string }, index: number) => void;
      onMouseLeave?: (entry: { model: string }, index: number) => void;
    }) => (
      <div data-testid="model-distribution-pie" data-key={dataKey}>
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
    Tooltip: ({ content }: { content: ReactNode }) => {
      if (!isValidElement<MockTooltipContentProps>(content)) {
        return null;
      }

      const dataKey = content.props.names?.requests ? "requests" : "costUsd";

      return cloneElement(content, {
        active: true,
        payload: [
          {
            dataKey,
            name: dataKey,
            value: dataKey === "requests" ? 2 : 42.02,
            color: "#3b82f6",
          },
        ],
      } as Record<string, unknown>);
    },
  };
});

describe("ModelDistributionDonut", () => {
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

  it("defaults to cost mode", () => {
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
    const tooltipRow = screen.getByText("Cost").parentElement;

    expect(tooltipRow).not.toBeNull();
    expect(within(tooltipRow as HTMLElement).getByText("Cost")).toBeInTheDocument();
    expect(within(tooltipRow as HTMLElement).getByText("$42.02")).toBeInTheDocument();
    expect(screen.getByText("$18.03")).toBeInTheDocument();
    expect(screen.getByTestId("model-distribution-pie")).toHaveAttribute("data-key", "costUsd");
  });

  it("switches to request mode for slices, tooltip, legend values, and percentages", async () => {
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

    const tooltipRow = screen.getByText("Requests").parentElement;

    expect(tooltipRow).not.toBeNull();
    expect(within(tooltipRow as HTMLElement).getByText("Requests")).toBeInTheDocument();
    expect(within(tooltipRow as HTMLElement).getByText("2")).toBeInTheDocument();
    expect(screen.getByText("20.0%")).toBeInTheDocument();
    expect(screen.getByText("80.0%")).toBeInTheDocument();
    expect(screen.getByText(/^8$/)).toBeInTheDocument();
    expect(screen.getByTestId("model-distribution-center-value")).toHaveTextContent("10");
    expect(screen.getByTestId("model-distribution-pie")).toHaveAttribute("data-key", "requests");
    expect(screen.getByRole("button", { name: /^cost$/i })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: /^req$/i })).toHaveAttribute("aria-pressed", "true");
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
});
