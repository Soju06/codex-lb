export function localDateISO(date: Date = new Date()): string {
  const localTime = date.getTime() - date.getTimezoneOffset() * 60_000;
  return new Date(localTime).toISOString().slice(0, 10);
}

export function daysAgoLocalISO(days: number, date: Date = new Date()): string {
  const shifted = new Date(date);
  shifted.setDate(shifted.getDate() - days);
  return localDateISO(shifted);
}

export function formatReportBucketDate(date: string): string {
  const [year, month, day] = date.split("-");
  if (!year || !month || !day) {
    return date;
  }
  return `${day}/${month}`;
}
