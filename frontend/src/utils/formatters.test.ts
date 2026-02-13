import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RESET_ERROR_LABEL } from "@/utils/constants";
import {
  formatAccessTokenLabel,
  formatCachedTokensMeta,
  formatCompactNumber,
  formatCountdown,
  formatCurrency,
  formatIdTokenLabel,
  formatModelLabel,
  formatNumber,
  formatPercent,
  formatPercentValue,
  formatQuotaResetLabel,
  formatQuotaResetMeta,
  formatRate,
  formatRefreshTokenLabel,
  formatRelative,
  formatTimeLong,
  formatTokensWithCached,
  formatWindowLabel,
  formatWindowMinutes,
  parseDate,
  toNumber,
  truncateText,
} from "@/utils/formatters";

describe("formatters", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("parses numbers safely", () => {
    expect(toNumber(42)).toBe(42);
    expect(toNumber("42.5")).toBe(42.5);
    expect(toNumber("")).toBeNull();
    expect(toNumber("abc")).toBeNull();
  });

  it("parses dates safely", () => {
    expect(parseDate("2026-01-01T00:00:00.000Z")).not.toBeNull();
    expect(parseDate("invalid-date")).toBeNull();
    expect(parseDate(null)).toBeNull();
  });

  it("formats number-like values", () => {
    expect(formatNumber(1200)).toBe("1,200");
    expect(formatCompactNumber(1200)).toMatch(/K$/);
    expect(formatCurrency(12)).toMatch(/^\$/);
    expect(formatNumber("abc")).toBe("--");
  });

  it("formats percent and rate values", () => {
    expect(formatPercent(49.6)).toBe("50%");
    expect(formatPercent(null)).toBe("0%");
    expect(formatPercentValue(49.6)).toBe(50);
    expect(formatPercentValue(null)).toBe(0);
    expect(formatRate(0.123)).toBe("12.3%");
    expect(formatRate(null)).toBe("--");
  });

  it("formats window labels", () => {
    expect(formatWindowMinutes(1440)).toBe("1d");
    expect(formatWindowMinutes(180)).toBe("3h");
    expect(formatWindowMinutes(30)).toBe("30m");
    expect(formatWindowMinutes(0)).toBe("--");
    expect(formatWindowLabel("primary", null)).toBe("5h");
    expect(formatWindowLabel("secondary", null)).toBe("7d");
  });

  it("formats token meta strings", () => {
    expect(formatTokensWithCached(1234, 200)).toContain("Cached");
    expect(formatTokensWithCached(1234, 0)).not.toContain("Cached");
    expect(formatCachedTokensMeta(1000, 250)).toBe("Cached: 250 (25%)");
    expect(formatCachedTokensMeta(0, 250)).toBe("Cached: --");
  });

  it("formats model and datetime labels", () => {
    expect(formatModelLabel("gpt-4.1", "high")).toBe("gpt-4.1 (high)");
    expect(formatModelLabel("gpt-4.1", null)).toBe("gpt-4.1");
    expect(formatModelLabel(null, null)).toBe("--");

    const formatted = formatTimeLong("2026-01-01T00:00:00.000Z");
    expect(formatted.time).not.toBe("--");
    expect(formatted.date).not.toBe("--");
  });

  it("formats relative and countdown values", () => {
    expect(formatRelative(30 * 60_000)).toBe("in 30m");
    expect(formatRelative(90 * 60_000)).toBe("in 2h");
    expect(formatRelative(30 * 60 * 60_000)).toBe("in 2d");
    expect(formatCountdown(125)).toBe("2:05");
  });

  it("formats quota reset labels", () => {
    const in30m = new Date(Date.now() + 30 * 60_000).toISOString();
    const inPast = new Date(Date.now() - 1_000).toISOString();
    expect(formatQuotaResetLabel(in30m)).toBe("in 30m");
    expect(formatQuotaResetLabel(inPast)).toBe("now");
    expect(formatQuotaResetLabel("1970-01-01T00:00:00.000Z")).toBe(RESET_ERROR_LABEL);
    expect(formatQuotaResetLabel("bad-date")).toBe(RESET_ERROR_LABEL);
    expect(formatQuotaResetMeta(null, null)).toBe("Quota reset unavailable");
  });

  it("truncates long text safely", () => {
    expect(truncateText("short", 10)).toBe("short");
    expect(truncateText("1234567890", 5)).toBe("12...");
    expect(truncateText(null, 5)).toBe("");
  });

  it("formats auth token status labels", () => {
    const future = new Date(Date.now() + 2 * 60 * 60_000).toISOString();

    expect(formatAccessTokenLabel(null)).toBe("Missing");
    expect(
      formatAccessTokenLabel({
        access: { expiresAt: "invalid-date" },
      }),
    ).toBe("Unknown");
    expect(
      formatAccessTokenLabel({
        access: { expiresAt: "1970-01-01T00:00:00.000Z" },
      }),
    ).toBe("Expired");
    expect(
      formatAccessTokenLabel({
        access: { expiresAt: future },
      }),
    ).toBe("Valid (in 2h)");

    expect(
      formatRefreshTokenLabel({
        refresh: { state: "stored" },
      }),
    ).toBe("Stored");
    expect(
      formatRefreshTokenLabel({
        refresh: { state: "expired" },
      }),
    ).toBe("Expired");
    expect(formatRefreshTokenLabel(undefined)).toBe("Unknown");

    expect(
      formatIdTokenLabel({
        idToken: { state: "parsed" },
      }),
    ).toBe("Parsed");
    expect(
      formatIdTokenLabel({
        idToken: { state: "unknown" },
      }),
    ).toBe("Unknown");
  });
});
