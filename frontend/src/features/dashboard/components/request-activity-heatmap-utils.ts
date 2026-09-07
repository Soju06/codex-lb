import type { RequestActivityDay } from "@/features/dashboard/schemas";

const DAY_MS = 24 * 60 * 60 * 1_000;

export type RequestActivityLevel = 0 | 1 | 2 | 3 | 4;

export type RequestActivityCalendarCell = {
  date: string;
  requests: number;
  level: RequestActivityLevel;
};

export type RequestActivityCalendarWeek = Array<RequestActivityCalendarCell | null>;

export type RequestActivityCalendar = {
  weeks: RequestActivityCalendarWeek[];
};

function parseDateOnly(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return null;
  }
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return date.getUTCFullYear() === Number(match[1]) &&
    date.getUTCMonth() === Number(match[2]) - 1 &&
    date.getUTCDate() === Number(match[3])
    ? date
    : null;
}

function startOfUtcDay(value: Date): Date {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate()));
}

function addDays(value: Date, amount: number): Date {
  return new Date(value.getTime() + amount * DAY_MS);
}

function formatDateOnly(value: Date): string {
  return value.toISOString().slice(0, 10);
}

function activityLevel(requests: number, maximumRequests: number): RequestActivityLevel {
  if (requests <= 0 || maximumRequests <= 0) {
    return 0;
  }
  return Math.min(4, Math.max(1, Math.ceil((requests / maximumRequests) * 4))) as RequestActivityLevel;
}

export function buildRequestActivityCalendar(
  days: RequestActivityDay[],
  today = new Date(),
): RequestActivityCalendar {
  const end = startOfUtcDay(today);
  const start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - 5, 1));
  const startDate = formatDateOnly(start);
  const endDate = formatDateOnly(end);
  const requestsByDate = new Map<string, number>();

  for (const day of days) {
    if (day.date < startDate || day.date > endDate || !parseDateOnly(day.date)) {
      continue;
    }
    requestsByDate.set(day.date, (requestsByDate.get(day.date) ?? 0) + day.requests);
  }

  const maximumRequests = Math.max(0, ...requestsByDate.values());
  const gridStart = addDays(start, -start.getUTCDay());
  const gridEnd = addDays(end, 6 - end.getUTCDay());
  const weeks: RequestActivityCalendarWeek[] = [];

  for (let weekStart = gridStart; weekStart <= gridEnd; weekStart = addDays(weekStart, 7)) {
    const week: RequestActivityCalendarWeek = [];
    for (let offset = 0; offset < 7; offset += 1) {
      const date = addDays(weekStart, offset);
      const dateValue = formatDateOnly(date);
      if (dateValue < startDate || dateValue > endDate) {
        week.push(null);
        continue;
      }
      const requests = requestsByDate.get(dateValue) ?? 0;
      week.push({
        date: dateValue,
        requests,
        level: activityLevel(requests, maximumRequests),
      });
    }
    weeks.push(week);
  }

  return { weeks };
}
