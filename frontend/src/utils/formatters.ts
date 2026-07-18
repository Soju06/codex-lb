import { RESET_ERROR_LABEL } from "@/utils/constants";
import { getTimeFormatPreference, type TimeFormatPreference } from "@/hooks/use-time-format";
import i18n from "@/i18n";

function t(key: string, options?: Record<string, unknown>): string {
  return i18n.t(key, options);
}

function getIntlLocale(): string {
  const language = (i18n.resolvedLanguage ?? i18n.language ?? "en").toLowerCase();
  if (language.startsWith("ko")) {
    return "ko-KR";
  }
  if (language.startsWith("zh")) {
    return "zh-CN";
  }
  return "en-US";
}

const numberFormatters = new Map<string, Intl.NumberFormat>();
const compactFormatters = new Map<string, Intl.NumberFormat>();
const currencyFormatters = new Map<string, Intl.NumberFormat>();
const dateFormatters = new Map<string, Intl.DateTimeFormat>();
const timeFormatters = new Map<string, Intl.DateTimeFormat>();
const chartDateTimeFormatters = new Map<string, Intl.DateTimeFormat>();

function getCachedFormatter<TFormatter>(
  cache: Map<string, TFormatter>,
  key: string,
  create: () => TFormatter,
): TFormatter {
  const cached = cache.get(key);
  if (cached) {
    return cached;
  }
  const formatter = create();
  cache.set(key, formatter);
  return formatter;
}

function getNumberFormatter(): Intl.NumberFormat {
  const locale = getIntlLocale();
  return getCachedFormatter(numberFormatters, locale, () => new Intl.NumberFormat(locale));
}

function getCompactFormatter(): Intl.NumberFormat {
  const locale = getIntlLocale();
  return getCachedFormatter(compactFormatters, locale, () => new Intl.NumberFormat(locale, {
    notation: "compact",
    maximumFractionDigits: 2,
  }));
}

function getCurrencyFormatter(): Intl.NumberFormat {
  const locale = getIntlLocale();
  return getCachedFormatter(currencyFormatters, locale, () => new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }));
}

function getDateFormatter(): Intl.DateTimeFormat {
  const locale = getIntlLocale();
  return getCachedFormatter(dateFormatters, locale, () => new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }));
}

function createTimeFormatter(locale: string, preference: TimeFormatPreference): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: preference === "12h" ? "h12" : "h23",
  });
}

function createChartDateTimeFormatter(locale: string, preference: TimeFormatPreference): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: preference === "12h" ? "h12" : "h23",
  });
}

export type FormattedDateTime = {
  time: string;
  date: string;
};

function getTimeFormatter(): Intl.DateTimeFormat {
  const locale = getIntlLocale();
  const preference = getTimeFormatPreference();
  return getCachedFormatter(timeFormatters, `${locale}:${preference}`, () => createTimeFormatter(locale, preference));
}

function getChartDateTimeFormatter(): Intl.DateTimeFormat {
  const locale = getIntlLocale();
  const preference = getTimeFormatPreference();
  return getCachedFormatter(
    chartDateTimeFormatters,
    `${locale}:${preference}`,
    () => createChartDateTimeFormatter(locale, preference),
  );
}

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
  return numeric === null ? "--" : getNumberFormatter().format(numeric);
}

export function formatCompactNumber(value: unknown): string {
  const numeric = toNumber(value);
  return numeric === null ? "--" : getCompactFormatter().format(numeric);
}

export function formatCurrency(value: unknown): string {
  const numeric = toNumber(value);
  return numeric === null ? "--" : getCurrencyFormatter().format(numeric);
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
  return t("formatters.cachedTokensInline", {
    total: formatCompactNumber(total),
    cached: formatCompactNumber(cached),
  });
}

export function formatCachedTokensMeta(totalTokens: unknown, cachedInputTokens: unknown): string {
  const total = toNumber(totalTokens);
  const cached = toNumber(cachedInputTokens);
  if (total === null || total <= 0 || cached === null || cached <= 0) {
    return t("formatters.cachedTokensMetaEmpty");
  }
  const percent = Math.min(100, Math.max(0, (cached / total) * 100));
  return t("formatters.cachedTokensMeta", {
    cached: formatCompactNumber(cached),
    percent: Math.round(percent),
  });
}

export function formatModelLabel(
  model: string | null | undefined,
  reasoningEffort: string | null | undefined,
  serviceTier?: string | null | undefined,
): string {
  const base = (model || "").trim();
  if (!base) {
    return "--";
  }
  const effort = (reasoningEffort || "").trim();
  const tier = (serviceTier || "").trim();
  const suffix = [effort, tier].filter(Boolean).join(", ");
  return suffix ? `${base} (${suffix})` : base;
}

export function formatTimeLong(iso: string | null | undefined): FormattedDateTime {
  const date = parseDate(iso);
  if (!date) {
    return { time: "--", date: "--" };
  }
  return {
    time: getTimeFormatter().format(date),
    date: getDateFormatter().format(date),
  };
}

export function formatDateTimeInline(iso: string | null | undefined): string {
  const formatted = formatTimeLong(iso);
  return formatted.time === "--" ? "--" : `${formatted.time} ${formatted.date}`;
}

function padTwo(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatLocalDateTimeSeconds(iso: string | null | undefined): string {
  const date = parseDate(iso);
  if (!date) {
    return "--";
  }
  return `${date.getFullYear()}-${padTwo(date.getMonth() + 1)}-${padTwo(date.getDate())} ${padTwo(date.getHours())}:${padTwo(date.getMinutes())}:${padTwo(date.getSeconds())}`;
}

export function formatChartDateTime(iso: string | null | undefined): string {
  const date = parseDate(iso);
  return date ? getChartDateTimeFormatter().format(date) : "--";
}

export function formatElapsed(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) {
    return "—";
  }
  if (ms < 1000) {
    return `${ms} ms`;
  }
  return `${(ms / 1000).toFixed(1)} s`;
}

export function formatRelative(ms: number): string {
  const minutes = Math.ceil(ms / 60_000);
  if (minutes < 60) {
    return t("formatters.relative.minutes", { count: minutes });
  }
  const hours = Math.ceil(minutes / 60);
  if (hours < 24) {
    return t("formatters.relative.hours", { count: hours });
  }
  const days = Math.ceil(hours / 24);
  return t("formatters.relative.days", { count: days });
}

export function formatResetRelative(ms: number): string {
  if (ms <= 60_000) {
    return t("formatters.relative.minutes", { count: 1 });
  }

  const totalMinutes = Math.floor(ms / 60_000);
  if (totalMinutes < 60) {
    return t("formatters.relative.minutes", { count: totalMinutes });
  }

  if (totalMinutes < 1440) {
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return minutes > 0
      ? t("formatters.relative.hoursMinutes", { hours, minutes })
      : t("formatters.relative.hours", { count: hours });
  }

  const totalHours = Math.floor(ms / 3_600_000);
  const days = Math.floor(totalHours / 24);
  const hours = totalHours % 24;
  return hours > 0
    ? t("formatters.relative.daysHours", { days, hours })
    : t("formatters.relative.days", { count: days });
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
    return t("formatters.now");
  }
  return formatResetRelative(diffMs);
}

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const MINUTE_MS = 60_000;
const EXPIRING_SOON_THRESHOLD_MS = 7 * DAY_MS;

export type SingleUnitRemaining = {
  label: string;
  expiringSoon: boolean;
};

export function formatSingleUnitRemaining(expiresAtIso: string): SingleUnitRemaining {
  const ms = new Date(expiresAtIso).getTime() - Date.now();
  if (ms <= 0) {
    return { label: t("formatters.now"), expiringSoon: true };
  }
  const days = Math.floor(ms / DAY_MS);
  const hours = Math.floor(ms / HOUR_MS);
  const minutes = Math.floor(ms / MINUTE_MS);
  const label =
    days >= 1
      ? `${days}d`
      : hours >= 1
        ? `${hours}h`
        : minutes >= 1
          ? `${minutes}m`
          : t("formatters.now");
  return { label, expiringSoon: ms < EXPIRING_SOON_THRESHOLD_MS };
}

export function formatQuotaResetMeta(
  resetAtSecondary: string | null | undefined,
  windowMinutesSecondary: unknown,
): string {
  const labelSecondary = formatQuotaResetLabel(resetAtSecondary);
  const windowSecondary = formatWindowLabel("secondary", windowMinutesSecondary);
  if (labelSecondary === RESET_ERROR_LABEL) {
    return t("formatters.quotaResetUnavailable");
  }
  return t("formatters.quotaResetMeta", { window: windowSecondary, label: labelSecondary });
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
    return t("formatters.token.missing");
  }
  const expiresDate = parseDate(expiresAt);
  if (!expiresDate) {
    return t("formatters.token.unknown");
  }
  const diffMs = expiresDate.getTime() - Date.now();
  if (diffMs <= 0) {
    return t("formatters.token.expired");
  }
  return t("formatters.token.valid", { relative: formatRelative(diffMs) });
}

export function formatRefreshTokenLabel(auth: AccountAuthStatus | null | undefined): string {
  const state = auth?.refresh?.state;
  const labelMap: Record<string, string> = {
    stored: t("formatters.token.stored"),
    missing: t("formatters.token.missing"),
    expired: t("formatters.token.expired"),
  };
  return state && labelMap[state] ? labelMap[state] : t("formatters.token.unknown");
}

export function formatIdTokenLabel(auth: AccountAuthStatus | null | undefined): string {
  const state = auth?.idToken?.state;
  const labelMap: Record<string, string> = {
    parsed: t("formatters.token.parsed"),
    unknown: t("formatters.token.unknown"),
  };
  return state && labelMap[state] ? labelMap[state] : t("formatters.token.unknown");
}
