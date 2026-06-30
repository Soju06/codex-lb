import type { DailyReportRow } from "./schemas";

export function buildContinuousDailyRows(
  startDate: string,
  endDate: string,
  rows: DailyReportRow[],
): DailyReportRow[] {
  if (!isISODate(startDate) || !isISODate(endDate) || startDate > endDate) {
    return rows;
  }

  const rowsByDate = new Map(rows.map((row) => [row.date, row]));
  const continuousRows: DailyReportRow[] = [];

  for (let current = startDate; current <= endDate; current = nextISODate(current)) {
    continuousRows.push(rowsByDate.get(current) ?? createZeroRow(current));
  }

  return continuousRows;
}

function nextISODate(date: string): string {
  const nextDate = new Date(`${date}T00:00:00Z`);
  nextDate.setUTCDate(nextDate.getUTCDate() + 1);
  return nextDate.toISOString().slice(0, 10);
}

function isISODate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }

  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function createZeroRow(date: string): DailyReportRow {
  return {
    date,
    requests: 0,
    inputTokens: 0,
    outputTokens: 0,
    cachedInputTokens: 0,
    costUsd: 0,
    activeAccounts: 0,
    errorCount: 0,
  };
}
