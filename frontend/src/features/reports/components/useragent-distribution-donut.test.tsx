import { cloneElement, isValidElement, type ReactNode } from "react";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UseragentDistributionDonut } from "./useragent-distribution-donut";

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
    }: {
      data: Array<{ useragent: string }>;
      dataKey: string;
    }) => (
      <div data-testid="useragent-distribution-pie" data-key={dataKey}>
        {data.map((entry) => (
          <div key={entry.useragent}>{entry.useragent}</div>
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
            value: dataKey === "requests" ? 8 : 12.5,
            color: "#3b82f6",
          },
        ],
      } as Record<string, unknown>);
    },
  };
});

describe("UseragentDistributionDonut", () => {
  it("defaults to cost mode", () => {
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
    const tooltipRow = screen.getByText("Cost").parentElement;

    expect(tooltipRow).not.toBeNull();
    expect(within(tooltipRow as HTMLElement).getByText("$12.50")).toBeInTheDocument();
    expect(screen.getByText("62.5%")).toBeInTheDocument();
    expect(screen.getByText("$7.50")).toBeInTheDocument();
    expect(screen.getByTestId("useragent-distribution-pie")).toHaveAttribute("data-key", "costUsd");
  });

  it("switches to request mode for slices, tooltip, legend values, and percentages", async () => {
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

    const tooltipRow = screen.getByText("Requests").parentElement;

    expect(tooltipRow).not.toBeNull();
    expect(within(tooltipRow as HTMLElement).getByText("8")).toBeInTheDocument();
    expect(screen.getByText("66.7%")).toBeInTheDocument();
    expect(screen.getByText("33.3%")).toBeInTheDocument();
    expect(screen.getByText(/^4$/)).toBeInTheDocument();
    expect(screen.getByTestId("useragent-distribution-pie")).toHaveAttribute("data-key", "requests");
  });
});
