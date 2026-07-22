const REPORTS_TIMEZONE_STORAGE_KEY = "codex-lb-reports-timezone";

function isValidTimeZone(timeZone: string | undefined): timeZone is string {
  if (!timeZone) {
    return false;
  }

  if (typeof Intl.supportedValuesOf === "function") {
    return timeZone === "UTC" || Intl.supportedValuesOf("timeZone").includes(timeZone);
  }

  try {
    new Intl.DateTimeFormat(undefined, { timeZone });
    return true;
  } catch {
    return false;
  }
}

export function getBrowserReportsTimeZone(): string | undefined {
  const detectedTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (isValidTimeZone(detectedTimeZone)) {
    try {
      window.localStorage.setItem(REPORTS_TIMEZONE_STORAGE_KEY, detectedTimeZone);
    } catch {
      // Some browser contexts block storage access; detection still succeeded.
    }
    return detectedTimeZone;
  }

  try {
    const cachedTimeZone = window.localStorage.getItem(REPORTS_TIMEZONE_STORAGE_KEY) ?? undefined;
    if (isValidTimeZone(cachedTimeZone)) {
      return cachedTimeZone;
    }
  } catch {
    // Some browser contexts block storage access; fall through and omit timezone.
  }

  return undefined;
}

export function localDateISO(date: Date = new Date()): string {
  const localTime = date.getTime() - date.getTimezoneOffset() * 60_000;
  return new Date(localTime).toISOString().slice(0, 10);
}

export function daysAgoLocalISO(days: number, date: Date = new Date()): string {
  const shifted = new Date(date);
  shifted.setDate(shifted.getDate() - days);
  return localDateISO(shifted);
}

export function isReportDateRangeValid(
  startDate: string | undefined,
  endDate: string | undefined,
): boolean {
  return !startDate || !endDate || startDate <= endDate;
}

export function formatReportBucketDate(date: string): string {
  const [year, month, day] = date.split("-");
  if (!year || !month || !day) {
    return date;
  }
  return `${year}-${month}-${day}`;
}
