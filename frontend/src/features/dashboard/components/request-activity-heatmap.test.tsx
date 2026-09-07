import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RequestActivityHeatmap } from "@/features/dashboard/components/request-activity-heatmap";
import { buildRequestActivityCalendar } from "@/features/dashboard/components/request-activity-heatmap-utils";

describe("request activity heatmap", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("builds a six-month, seven-day calendar and scales activity levels", () => {
    const calendar = buildRequestActivityCalendar(
      [
        { date: "2026-09-10", requests: 8 },
        { date: "2026-09-11", requests: 2 },
      ],
      new Date("2026-09-15T12:00:00Z"),
    );

    expect(calendar.weeks.every((week) => week.length === 7)).toBe(true);
    const cells = calendar.weeks.flat().filter((cell) => cell !== null);
    expect(cells).toHaveLength(168);
    expect(cells.find((cell) => cell?.date === "2026-09-10")).toMatchObject({
      requests: 8,
      level: 4,
    });
    expect(cells.find((cell) => cell?.date === "2026-09-11")).toMatchObject({
      requests: 2,
      level: 1,
    });
    expect(cells.find((cell) => cell?.date === "2026-09-12")).toMatchObject({
      requests: 0,
      level: 0,
    });
  });

  it("renders localized tooltip content without axis labels", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-15T12:00:00Z"));

    render(
      <RequestActivityHeatmap
        days={[{ date: "2026-09-10", requests: 8 }]}
      />,
    );

    expect(screen.getByTestId("request-activity-heatmap")).toBeInTheDocument();
    expect(screen.queryByTestId("request-activity-month-label")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2026-09-10 · 8 requests" })).toHaveAttribute(
      "data-level",
      "4",
    );
  });
});
