import { RESET_ERROR_LABEL } from "@/utils/constants";

const numberFormatter = new Intl.NumberFormat("en-US");
const compactFormatter = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});
const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const timeFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});
const dateFormatter = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export type FormattedDateTime = {
  time: string;
  date: string;
};

type TokenState = {
  state?: string | null;
};

type AccessTokenState = {
  expiresAt?: string | null;
};

export type AccountAuthStatus = {
  access?: AccessTokenState | null;
  refresh?: TokenState | null;
  idToken?: TokenState | null;
};

export function formatSlug(value: string): string {
  if (!value) return "";
  const words = value.split("_");
  words[0] = words[0].charAt(0).toUpperCase() + words[0].slice(1);
  return words.join(" ");
}

export function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatNumber(value: unknown): string {
  const numeric = toNumber(value);
  return numeric === null ? "--" : numberFormatter.format(numeric);
}

export function formatCompactNumber(value: unknown): string {
  const numeric = toNumber(value);
  return numeric === null ? "--" : compactFormatter.format(numeric);
}

export function formatCurrency(value: unknown): string {
  const numeric = toNumber(value);
  return numeric === null ? "--" : currencyFormatter.format(numeric);
}

export function formatPercent(value: unknown): string {
  const numeric = toNumber(value);
  if (numeric === null) {
    return "0%";
  }
  return `${Math.round(numeric)}%`;
}

export function formatPercentNullable(value: unknown): string {
  const numeric = toNumber(value);
  if (numeric === null) {
    return "--";
  }
  return `${Math.round(numeric)}%`;
}

export function formatPercentValue(value: unknown): number {
  const numeric = toNumber(value);
  return numeric === null ? 0 : Math.round(numeric);
}

export function formatRate(value: unknown): string {
  const numeric = toNumber(value);
  return numeric === null ? "--" : `${(numeric * 100).toFixed(1)}%`;
}

export function formatWindowMinutes(value: unknown): string {
  const minutes = toNumber(value);
  if (minutes === null || minutes <= 0) {
    return "--";
  }
  if (minutes % 1440 === 0) {
    return `${minutes / 1440}d`;
  }
  if (minutes % 60 === 0) {
    return `${minutes / 60}h`;
  }
  return `${minutes}m`;
}

export function formatWindowLabel(
  key: "primary" | "secondary" | string,
  minutes: unknown,
): string {
  const formatted = formatWindowMinutes(minutes);
  if (formatted !== "--") {
    return formatted;
  }
  if (key === "secondary") {
    return "7d";
  }
  if (key === "primary") {
    return "5h";
  }
  return "--";
}

export function formatTokensWithCached(totalTokens: unknown, cachedInputTokens: unknown): string {
  const total = toNumber(totalTokens);
  if (total === null) {
    return "--";
  }
  const cached = toNumber(cachedInputTokens);
  if (cached === null || cached <= 0) {
    return formatCompactNumber(total);
  }
  return `${formatCompactNumber(total)} (${formatCompactNumber(cached)} Cached)`;
}

export function formatCachedTokensMeta(totalTokens: unknown, cachedInputTokens: unknown): string {
  const total = toNumber(totalTokens);
  const cached = toNumber(cachedInputTokens);
  if (total === null || total <= 0 || cached === null || cached <= 0) {
    return "Cached: --";
  }
  const percent = Math.min(100, Math.max(0, (cached / total) * 100));
  return `Cached: ${formatCompactNumber(cached)} (${Math.round(percent)}%)`;
}

export function formatModelLabel(model: string | null | undefined, reasoningEffort: string | null | undefined): string {
  const base = (model || "").trim();
  if (!base) {
    return "--";
  }
  const effort = (reasoningEffort || "").trim();
  return effort ? `${base} (${effort})` : base;
}

export function formatTimeLong(iso: string | null | undefined): FormattedDateTime {
  const date = parseDate(iso);
  if (!date) {
    return { time: "--", date: "--" };
  }
  return {
    time: timeFormatter.format(date),
    date: dateFormatter.format(date),
  };
}

export function formatRelative(ms: number): string {
  const minutes = Math.ceil(ms / 60_000);
  if (minutes < 60) {
    return `in ${minutes}m`;
  }
  const hours = Math.ceil(minutes / 60);
  if (hours < 24) {
    return `in ${hours}h`;
  }
  const days = Math.ceil(hours / 24);
  return `in ${days}d`;
}

export function formatCountdown(seconds: number): string {
  const clamped = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(clamped / 60);
  const remainder = clamped % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

export function formatQuotaResetLabel(resetAt: string | null | undefined): string {
  const date = parseDate(resetAt);
  if (!date || date.getTime() <= 0) {
    return RESET_ERROR_LABEL;
  }
  const diffMs = date.getTime() - Date.now();
  if (diffMs <= 0) {
    return "now";
  }
  return formatRelative(diffMs);
}

export function formatQuotaResetMeta(
  resetAtSecondary: string | null | undefined,
  windowMinutesSecondary: unknown,
): string {
  const labelSecondary = formatQuotaResetLabel(resetAtSecondary);
  const windowSecondary = formatWindowLabel("secondary", windowMinutesSecondary);
  if (labelSecondary === RESET_ERROR_LABEL) {
    return "Quota reset unavailable";
  }
  return `Quota reset (${windowSecondary}) · ${labelSecondary}`;
}

export function truncateText(value: unknown, maxLen = 80): string {
  if (value === null || value === undefined) {
    return "";
  }
  const text = String(value);
  if (text.length <= maxLen) {
    return text;
  }
  if (maxLen <= 3) {
    return text.slice(0, maxLen);
  }
  return `${text.slice(0, maxLen - 1)}\u2026`;
}

export function formatAccessTokenLabel(auth: AccountAuthStatus | null | undefined): string {
  const expiresAt = auth?.access?.expiresAt;
  if (!expiresAt) {
    return "Missing";
  }
  const expiresDate = parseDate(expiresAt);
  if (!expiresDate) {
    return "Unknown";
  }
  const diffMs = expiresDate.getTime() - Date.now();
  if (diffMs <= 0) {
    return "Expired";
  }
  return `Valid (${formatRelative(diffMs)})`;
}

export function formatRefreshTokenLabel(auth: AccountAuthStatus | null | undefined): string {
  const state = auth?.refresh?.state;
  const labelMap: Record<string, string> = {
    stored: "Stored",
    missing: "Missing",
    expired: "Expired",
  };
  return state && labelMap[state] ? labelMap[state] : "Unknown";
}

export function formatIdTokenLabel(auth: AccountAuthStatus | null | undefined): string {
  const state = auth?.idToken?.state;
  const labelMap: Record<string, string> = {
    parsed: "Parsed",
    unknown: "Unknown",
  };
  return state && labelMap[state] ? labelMap[state] : "Unknown";
}

export function toModels(value: string): string[] | undefined {
  const values = value
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  return values.length > 0 ? values : undefined;
}

export function toModelsNullable(value: string): string[] | null {
  const values = value
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  return values.length > 0 ? values : null;
}

export function toIsoDateTime(value: string): string | undefined {
  if (!value) {
    return undefined;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return undefined;
  }
  return date.toISOString();
}

export function toIsoDateTimeNullable(value: string): string | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date.toISOString();
}

export function toLocalDateTime(value: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const offset = date.getTimezoneOffset();
  const adjusted = new Date(date.getTime() - offset * 60_000);
  return adjusted.toISOString().slice(0, 16);
}
