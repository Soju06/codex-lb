import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";

import { AccountTrendChart } from "@/features/accounts/components/account-trend-chart";

vi.mock("@/components/lazy-recharts", () => ({
  Area: () => null,
  AreaChart: ({ children, data }: { children: ReactNode; data: unknown }) => (
    <div><output data-testid="chart-observations">{JSON.stringify(data)}</output>{children}</div>
  ),
  CartesianGrid: () => null,
  Line: () => null,
  ResponsiveContainer: ({ children }: { children: ReactNode }) => (
    <div data-testid="responsive-container" style={{ width: 400, height: 200 }}>
      {children}
    </div>
  ),
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

const BASE = new Date("2026-01-15T00:00:00Z");

function makePoints(count: number, baseValue: number) {
  return Array.from({ length: count }, (_, i) => ({
    t: new Date(BASE.getTime() + i * 3600_000).toISOString(),
    v: baseValue + i * 0.5,
  }));
}

describe("AccountTrendChart", () => {
  it("renders empty state when no data is provided", () => {
    render(<AccountTrendChart primary={[]} secondary={[]} />);
    expect(screen.getByText("No trend data available")).toBeInTheDocument();
  });

  it("renders chart container when data is provided", () => {
    const primary = makePoints(24, 70);
    const secondary = makePoints(24, 50);

    render(<AccountTrendChart primary={primary} secondary={secondary} />);
    expect(screen.getByTestId("responsive-container")).toBeInTheDocument();
  });

  it("renders chart with only primary data when secondary is empty", () => {
    const primary = makePoints(24, 80);

    render(<AccountTrendChart primary={primary} secondary={[]} />);
    expect(screen.getByTestId("responsive-container")).toBeInTheDocument();
  });
  it("retains non-aligned monthly observations without inventing zero remaining", () => {
    const first = "2026-01-15T00:00:00Z";
    const second = "2026-01-15T01:00:00Z";
    render(<AccountTrendChart
      primary={[{ t: second, v: 60 }]}
      secondary={[{ t: first, v: 85 }]}
      monthly
    />);
    expect(JSON.parse(screen.getByTestId("chart-observations").textContent ?? "[]")).toEqual([
      { t: new Date(first).toISOString(), primary: null, secondary: 85 },
      { t: new Date(second).toISOString(), primary: 60, secondary: 85 },
    ]);
  });

  it("combines equivalent instants across offsets and retains the scheduled value", () => {
    render(<AccountTrendChart
      primary={[{ t: "2026-01-15T00:00:00Z", v: 60 }]}
      secondary={[{ t: "2026-01-14T19:00:00-05:00", v: 85 }]}
      secondaryScheduled={[{ t: "2026-01-14T19:00:00-05:00", v: 80 }]}
    />);
    expect(JSON.parse(screen.getByTestId("chart-observations").textContent ?? "[]")).toEqual([
      { t: "2026-01-15T00:00:00.000Z", primary: 60, secondary: 85, secondaryScheduled: 80 },
    ]);
  });

  it("interpolates between observations while preserving observed zero", () => {
    const times = Array.from({ length: 5 }, (_, i) =>
      new Date(BASE.getTime() + i * 3600_000).toISOString(),
    );
    render(<AccountTrendChart
      primary={[{ t: times[4], v: 50 }, { t: times[0], v: 60 }]}
      secondary={[{ t: times[0], v: 90 }, { t: times[2], v: 0 }, { t: times[4], v: 85 }]}
      secondaryScheduled={[{ t: times[1], v: 80 }, { t: times[3], v: 70 }]}
    />);
    expect(JSON.parse(screen.getByTestId("chart-observations").textContent ?? "[]")).toEqual([
      { t: times[0], primary: 60, secondary: 90 },
      { t: times[1], primary: 57.5, secondary: 45, secondaryScheduled: 80 },
      { t: times[2], primary: 55, secondary: 0 },
      { t: times[3], primary: 52.5, secondary: 42.5, secondaryScheduled: 70 },
      { t: times[4], primary: 50, secondary: 85 },
    ]);
  });

  it("replaces trailing carry-forward with time-weighted interpolation when a new sample arrives", () => {
    const start = "2026-01-15T00:00:00Z";
    const middle = "2026-01-15T01:00:00Z";
    const end = "2026-01-15T04:00:00Z";
    const primary = [{ t: middle, v: 50 }];
    const { rerender } = render(<AccountTrendChart
      primary={primary} secondary={[{ t: start, v: 90 }]}
    />);
    expect(JSON.parse(screen.getByTestId("chart-observations").textContent ?? "[]")[1])
      .toEqual({ t: new Date(middle).toISOString(), primary: 50, secondary: 90 });
    rerender(<AccountTrendChart
      primary={primary} secondary={[{ t: start, v: 90 }, { t: end, v: 70 }]}
    />);
    expect(JSON.parse(screen.getByTestId("chart-observations").textContent ?? "[]")).toEqual([
      { t: new Date(start).toISOString(), primary: null, secondary: 90 },
      { t: new Date(middle).toISOString(), primary: 50, secondary: 85 },
      { t: new Date(end).toISOString(), primary: 50, secondary: 70 },
    ]);
  });

});
