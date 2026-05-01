import { describe, expect, it } from "vitest";

import {
  formatScheduleTimeForInput,
  parseScheduleTimeInput,
  scheduleTimePlaceholder,
} from "@/features/automations/time-utils";

describe("automations time utils", () => {
  it("parses 24h values into canonical HH:MM", () => {
    expect(parseScheduleTimeInput("05:00", "24h")).toEqual({ ok: true, value: "05:00" });
    expect(parseScheduleTimeInput("5:07", "24h")).toEqual({ ok: true, value: "05:07" });
  });

  it("parses 12h values with AM/PM into canonical HH:MM", () => {
    expect(parseScheduleTimeInput("05:00 AM", "12h")).toEqual({ ok: true, value: "05:00" });
    expect(parseScheduleTimeInput("12:30 PM", "12h")).toEqual({ ok: true, value: "12:30" });
    expect(parseScheduleTimeInput("12:30 am", "12h")).toEqual({ ok: true, value: "00:30" });
  });

  it("returns invalid/empty errors for malformed input", () => {
    expect(parseScheduleTimeInput("", "24h")).toEqual({ ok: false, reason: "empty" });
    expect(parseScheduleTimeInput("25:00", "24h")).toEqual({ ok: false, reason: "invalid" });
    expect(parseScheduleTimeInput("13:00", "12h")).toEqual({ ok: false, reason: "invalid" });
  });

  it("formats canonical HH:MM to display based on time format preference", () => {
    expect(formatScheduleTimeForInput("17:05", "24h")).toBe("17:05");
    expect(formatScheduleTimeForInput("17:05", "12h")).toBe("05:05 PM");
  });

  it("returns placeholders matching time format preference", () => {
    expect(scheduleTimePlaceholder("24h")).toBe("HH:MM");
    expect(scheduleTimePlaceholder("12h")).toBe("HH:MM AM");
  });
});
