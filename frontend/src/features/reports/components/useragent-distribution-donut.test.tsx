import type { ReactNode } from "react";
import userEvent from "@testing-library/user-event";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UseragentDistributionDonut } from "./useragent-distribution-donut";

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
      data: Array<{ useragent: string }>;
      dataKey: string;
      onMouseEnter?: (entry: { useragent: string }, index: number) => void;
      onMouseLeave?: (entry: { useragent: string }, index: number) => void;
      shape?: unknown;
    }) => (
      <div
        data-testid="useragent-distribution-pie"
        data-key={dataKey}
        data-shape={shape ? "true" : "false"}
      >
        {data.map((entry, index) => (
          <button
            key={entry.useragent}
            type="button"
            data-testid={`useragent-slice-${index}`}
            onMouseEnter={() => onMouseEnter?.(entry, index)}
            onMouseLeave={() => onMouseLeave?.(entry, index)}
          >
            {entry.useragent}
          </button>
        ))}
      </div>
    ),
    Cell: () => null,
  };
});

describe("UseragentDistributionDonut", () => {
  it("highlights the matching legend row when a legend item is hovered", () => {
    render(
      <UseragentDistributionDonut
        data={[
          { useragent: "CLI", costUsd: 12.5, requests: 8, percentage: 62.5 },
          { useragent: "SDK", costUsd: 7.5, requests: 4, percentage: 37.5 },
        ]}
      />,
    );

    const legendRow = screen.getByTestId("useragent-distribution-legend-0");

    expect(screen.getByTestId("useragent-distribution-pie")).toHaveAttribute("data-shape", "true");
    expect(legendRow).toHaveAttribute("data-active", "false");

    fireEvent.mouseEnter(legendRow);
    expect(legendRow).toHaveAttribute("data-active", "true");

    fireEvent.mouseLeave(legendRow);
    expect(legendRow).toHaveAttribute("data-active", "false");
  });

  it("highlights the matching legend row when a pie slice is hovered", () => {
    render(
      <UseragentDistributionDonut
        data={[
          { useragent: "CLI", costUsd: 12.5, requests: 8, percentage: 62.5 },
          { useragent: "SDK", costUsd: 7.5, requests: 4, percentage: 37.5 },
        ]}
      />,
    );

    fireEvent.mouseEnter(screen.getByTestId("useragent-slice-0"));
    expect(screen.getByTestId("useragent-distribution-legend-0")).toHaveAttribute("data-active", "true");
  });

  it("limits the legend viewport to four visible rows before scrolling", () => {
    render(
      <UseragentDistributionDonut
        data={Array.from({ length: 5 }, (_, index) => ({
          useragent: `UA-${index + 1}`,
          costUsd: index + 1,
          requests: index + 1,
          percentage: 20,
        }))}
      />,
    );

    expect(screen.getByTestId("useragent-distribution-legend-list")).toHaveStyle({
      maxHeight: "calc(4 * 2rem)",
    });
    expect(screen.getByTestId("useragent-distribution-legend-4")).toBeInTheDocument();
  });

  it("shows the total label and compact cost total in the donut center by default", () => {
    render(
      <UseragentDistributionDonut
        data={[
          { useragent: "CLI", costUsd: 430, requests: 8, percentage: 30 },
          { useragent: "SDK", costUsd: 1000, requests: 4, percentage: 70 },
        ]}
      />,
    );

    expect(screen.getByTestId("useragent-distribution-center-label")).toHaveTextContent("Total");
    expect(screen.getByTestId("useragent-distribution-center-value")).toHaveTextContent("$1.43K");
  });

  it("renders Missing User-Agent with a fixed grey legend dot", () => {
    render(
      <UseragentDistributionDonut
        data={[
          { useragent: "Missing User-Agent", costUsd: 12.5, requests: 8, percentage: 62.5 },
          { useragent: "SDK", costUsd: 7.5, requests: 4, percentage: 37.5 },
        ]}
      />,
    );

    const unknownLegendLabel = screen.getAllByText("Missing User-Agent").at(-1);
    const unknownLegendRow = unknownLegendLabel?.closest("div.flex.items-center.gap-2");

    expect(unknownLegendLabel).toBeDefined();
    expect(unknownLegendRow).not.toBeNull();
    expect((unknownLegendRow?.firstElementChild as HTMLElement) ?? null).toHaveStyle({
      background: "#9ca3af",
    });
  });

  it("keeps a real Unknown bucket on the normal palette", () => {
    render(
      <UseragentDistributionDonut
        data={[
          { useragent: "Unknown", costUsd: 12.5, requests: 8, percentage: 62.5 },
          { useragent: "SDK", costUsd: 7.5, requests: 4, percentage: 37.5 },
        ]}
      />,
    );

    const unknownLegendLabel = screen.getAllByText("Unknown").at(-1);
    const unknownLegendRow = unknownLegendLabel?.closest("div.flex.items-center.gap-2");

    expect(unknownLegendLabel).toBeDefined();
    expect(unknownLegendRow).not.toBeNull();
    expect((unknownLegendRow?.firstElementChild as HTMLElement) ?? null).toHaveStyle({
      background: "#3b82f6",
    });
  });

  it("pads legend value cells to the longest formatted request total", async () => {
    const user = userEvent.setup();

    render(
      <UseragentDistributionDonut
        data={[
          { useragent: "CLI", costUsd: 12.5, requests: 8, percentage: 10 },
          { useragent: "SDK", costUsd: 120.75, requests: 1200, percentage: 90 },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^req$/i }));

    const smallRequestLegendValue = screen.getAllByText(/^8$/).at(-1);
    const largeRequestLegendValue = screen.getAllByText("1.2K").at(-1);

    expect(smallRequestLegendValue).toBeDefined();
    expect(largeRequestLegendValue).toBeDefined();
    expect(smallRequestLegendValue).toHaveStyle({ minWidth: "4ch" });
    expect(largeRequestLegendValue).toHaveStyle({ minWidth: "4ch" });
  });

  it("defaults to cost mode without rendering a donut tooltip", () => {
    render(
      <UseragentDistributionDonut
        data={[
          { useragent: "CLI", costUsd: 12.5, requests: 8, percentage: 62.5 },
          { useragent: "SDK", costUsd: 7.5, requests: 4, percentage: 37.5 },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: /^cost$/i })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^req$/i })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("useragent-distribution-center-value")).toHaveTextContent("$20");
    expect(screen.getByText("62.5%")).toBeInTheDocument();
    expect(screen.getByText("$7.5")).toBeInTheDocument();
    expect(screen.getByTestId("useragent-distribution-pie")).toHaveAttribute("data-key", "costUsd");
    expect(screen.queryByText(/^Cost$/)).not.toBeInTheDocument();
  });

  it("switches to request mode for slices, legend values, and percentages without rendering a donut tooltip", async () => {
    const user = userEvent.setup();

    render(
      <UseragentDistributionDonut
        data={[
          { useragent: "CLI", costUsd: 12.5, requests: 8, percentage: 62.5 },
          { useragent: "SDK", costUsd: 7.5, requests: 4, percentage: 37.5 },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^req$/i }));

    expect(screen.getByText("66.7%")).toBeInTheDocument();
    expect(screen.getByText("33.3%")).toBeInTheDocument();
    expect(screen.getByText(/^4$/)).toBeInTheDocument();
    expect(screen.getByTestId("useragent-distribution-center-value")).toHaveTextContent("12");
    expect(screen.getByTestId("useragent-distribution-pie")).toHaveAttribute("data-key", "requests");
    expect(screen.queryByText(/^Requests$/)).not.toBeInTheDocument();
  });

  it("uses compact request totals in the center and legend when request mode is active", async () => {
    const user = userEvent.setup();

    render(
      <UseragentDistributionDonut
        data={[
          { useragent: "CLI", costUsd: 12.5, requests: 500_000_000, percentage: 40 },
          { useragent: "SDK", costUsd: 7.5, requests: 1_000_000_000, percentage: 60 },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^req$/i }));

    expect(screen.getByTestId("useragent-distribution-center-value")).toHaveTextContent("1.5B");
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
      <UseragentDistributionDonut
        data={Array.from({ length: 5 }, (_, index) => ({
          useragent: `UA-${index + 1}`,
          costUsd: 5 - index,
          requests: index + 1,
          percentage: 20,
        }))}
      />,
    );

    fireEvent.mouseEnter(screen.getByTestId("useragent-slice-4"));

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
  });
});
