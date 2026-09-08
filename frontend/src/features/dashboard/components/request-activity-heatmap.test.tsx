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

  it("uses the browser-local calendar date at UTC day boundaries", () => {
    const today = new Date("2026-01-01T00:30:00Z");

    const calendar = buildRequestActivityCalendar(
      [
        { date: "2025-07-01", requests: 3 },
        { date: "2025-12-31", requests: 7 },
        { date: "2026-01-01", requests: 11 },
      ],
      today,
      "America/Los_Angeles",
    );
    const cells = calendar.weeks.flat().filter((cell) => cell !== null);

    expect(cells[0]?.date).toBe("2025-07-01");
    expect(cells.at(-1)?.date).toBe("2025-12-31");
    expect(cells.find((cell) => cell?.date === "2025-12-31")).toMatchObject({ requests: 7 });
    expect(cells.find((cell) => cell?.date === "2026-01-01")).toBeUndefined();
  });

  it("uses the requested IANA timezone for calendar dates across DST", () => {
    const calendar = buildRequestActivityCalendar(
      [
        { date: "2026-03-08", requests: 3 },
        { date: "2026-03-09", requests: 7 },
      ],
      new Date("2026-03-09T00:30:00Z"),
      "America/Los_Angeles",
    );
    const cells = calendar.weeks.flat().filter((cell) => cell !== null);

    expect(cells.at(-1)?.date).toBe("2026-03-08");
    expect(cells.find((cell) => cell?.date === "2026-03-08")).toMatchObject({ requests: 3 });
    expect(cells.find((cell) => cell?.date === "2026-03-09")).toBeUndefined();
  });

  it("defaults calendar date derivation to UTC", () => {
    const calendar = buildRequestActivityCalendar(
      [{ date: "2026-01-01", requests: 5 }],
      new Date("2026-01-01T00:30:00Z"),
    );
    const cells = calendar.weeks.flat().filter((cell) => cell !== null);

    expect(cells.at(-1)?.date).toBe("2026-01-01");
    expect(cells.find((cell) => cell?.date === "2026-01-01")).toMatchObject({ requests: 5 });
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
