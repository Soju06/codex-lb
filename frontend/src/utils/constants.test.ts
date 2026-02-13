import { describe, expect, it } from "vitest";

import {
  DONUT_COLORS,
  ERROR_LABELS,
  KNOWN_PLAN_TYPES,
  MESSAGE_TONE_META,
  ROUTING_LABELS,
  STATUS_LABELS,
} from "@/utils/constants";

describe("STATUS_LABELS", () => {
  it("contains expected status mappings", () => {
    expect(Object.keys(STATUS_LABELS).sort()).toEqual([
      "active",
      "deactivated",
      "exceeded",
      "limited",
      "paused",
    ]);
    expect(STATUS_LABELS.active).toBe("Active");
    expect(STATUS_LABELS.exceeded).toBe("Quota exceeded");
  });
});

describe("ERROR_LABELS", () => {
  it("maps known error codes to normalized labels", () => {
    expect(ERROR_LABELS.rate_limit).toBe("rate limit");
    expect(ERROR_LABELS.rate_limit_exceeded).toBe("rate limit");
    expect(ERROR_LABELS.quota_exceeded).toBe("quota");
    expect(ERROR_LABELS.insufficient_quota).toBe("quota");
    expect(ERROR_LABELS.upstream_error).toBe("upstream");
  });
});

describe("ROUTING_LABELS", () => {
  it("contains supported routing labels", () => {
    expect(ROUTING_LABELS.usage_weighted).toBe("usage weighted");
    expect(ROUTING_LABELS.round_robin).toBe("round robin");
    expect(ROUTING_LABELS.sticky).toBe("sticky");
  });
});

describe("KNOWN_PLAN_TYPES", () => {
  it("contains canonical plan values", () => {
    expect(KNOWN_PLAN_TYPES.has("free")).toBe(true);
    expect(KNOWN_PLAN_TYPES.has("pro")).toBe(true);
    expect(KNOWN_PLAN_TYPES.has("enterprise")).toBe(true);
    expect(KNOWN_PLAN_TYPES.has("nonexistent")).toBe(false);
  });
});

describe("DONUT_COLORS", () => {
  it("contains hex color palette entries", () => {
    expect(DONUT_COLORS.length).toBeGreaterThanOrEqual(6);
    for (const color of DONUT_COLORS) {
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });
});

describe("MESSAGE_TONE_META", () => {
  it("contains complete metadata for all tones", () => {
    expect(Object.keys(MESSAGE_TONE_META).sort()).toEqual([
      "error",
      "info",
      "question",
      "success",
      "warning",
    ]);
    expect(MESSAGE_TONE_META.success.defaultTitle).toBe("Import complete");
    expect(MESSAGE_TONE_META.error.className).toBe("deactivated");
  });
});
