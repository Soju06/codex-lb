import type { TimeFormatPreference } from "@/hooks/use-time-format";

type ParseScheduleTimeResult =
  | { ok: true; value: string }
  | { ok: false; reason: "empty" | "invalid" };

const TWELVE_HOUR_REGEX = /^(\d{1,2}):([0-5]\d)\s*([ap]m)$/i;
const TWENTY_FOUR_HOUR_REGEX = /^([01]?\d|2[0-3]):([0-5]\d)$/;

export function parseScheduleTimeInput(value: string, timeFormat: TimeFormatPreference): ParseScheduleTimeResult {
  const normalized = value.trim();
  if (!normalized) {
    return { ok: false, reason: "empty" };
  }

  if (timeFormat === "24h") {
    const match = TWENTY_FOUR_HOUR_REGEX.exec(normalized);
    if (!match) {
      return { ok: false, reason: "invalid" };
    }
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    return { ok: true, value: `${hour.toString().padStart(2, "0")}:${minute.toString().padStart(2, "0")}` };
  }

  const match = TWELVE_HOUR_REGEX.exec(normalized);
  if (!match) {
    return { ok: false, reason: "invalid" };
  }

  const hour12 = Number(match[1]);
  const minute = Number(match[2]);
  const meridiem = match[3].toUpperCase();
  if (hour12 < 1 || hour12 > 12) {
    return { ok: false, reason: "invalid" };
  }

  const hour24 = hour12 % 12 + (meridiem === "PM" ? 12 : 0);
  return { ok: true, value: `${hour24.toString().padStart(2, "0")}:${minute.toString().padStart(2, "0")}` };
}

export function formatScheduleTimeForInput(value: string, timeFormat: TimeFormatPreference): string {
  const match = /^(\d{2}):(\d{2})$/.exec(value.trim());
  if (!match) {
    return value.trim();
  }

  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return value.trim();
  }

  if (timeFormat === "24h") {
    return `${match[1]}:${match[2]}`;
  }

  const hour12 = ((hour + 11) % 12) + 1;
  const suffix = hour >= 12 ? "PM" : "AM";
  return `${hour12.toString().padStart(2, "0")}:${match[2]} ${suffix}`;
}

export function scheduleTimePlaceholder(timeFormat: TimeFormatPreference): string {
  return timeFormat === "24h" ? "HH:MM" : "HH:MM AM";
}
