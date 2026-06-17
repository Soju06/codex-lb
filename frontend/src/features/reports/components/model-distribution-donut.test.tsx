import { cloneElement, isValidElement, type ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
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
      if (!isValidElement(content)) {
        return null;
      }

      return cloneElement(content, {
        active: true,
        payload: [{ dataKey: "costUsd", name: "costUsd", value: 42.02, color: "#3b82f6" }],
      } as Record<string, unknown>);
    },
  };
});

describe("ModelDistributionDonut", () => {
  it("does not render a center cost value", () => {
    render(
      <ModelDistributionDonut
        data={[
          { model: "gpt-5", costUsd: 42.02, percentage: 70 },
          { model: "o3", costUsd: 18.03, percentage: 30 },
        ]}
      />,
    );

    expect(screen.queryByTestId("model-distribution-center-cost")).not.toBeInTheDocument();
    const tooltipRow = screen.getByText("Cost").parentElement;

    expect(tooltipRow).not.toBeNull();
    expect(within(tooltipRow as HTMLElement).getByText("Cost")).toBeInTheDocument();
    expect(within(tooltipRow as HTMLElement).getByText("$42.02")).toBeInTheDocument();
    expect(screen.getByText("$18.03")).toBeInTheDocument();
    expect(screen.getByTestId("model-distribution-pie")).toHaveAttribute("data-key", "costUsd");
  });
});
