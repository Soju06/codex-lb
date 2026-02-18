import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { AccountTrendChart } from "@/features/accounts/components/account-trend-chart";

// Mock recharts to avoid SVG rendering issues in jsdom
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="responsive-container" style={{ width: 400, height: 200 }}>
        {children}
      </div>
    ),
  };
});

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
});
