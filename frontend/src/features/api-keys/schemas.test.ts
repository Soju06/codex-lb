import { describe, expect, it } from "vitest";

import {
  ApiKeyCreateResponseSchema,
  ApiKeySchema,
  ApiKeyUpdateRequestSchema,
  LimitRuleCreateSchema,
} from "@/features/api-keys/schemas";

const ISO = "2026-01-01T00:00:00+00:00";

describe("ApiKeySchema", () => {
  it("parses api key entity payload with limits", () => {
    const parsed = ApiKeySchema.parse({
      id: "key-1",
      name: "Service Key",
      keyPrefix: "sk-live",
      allowedModels: ["gpt-4.1"],
      expiresAt: null,
      isActive: true,
      createdAt: ISO,
      lastUsedAt: ISO,
      limits: [
        {
          id: 1,
          limitType: "total_tokens",
          limitWindow: "weekly",
          maxValue: 100000,
          currentValue: 1200,
          modelFilter: null,
          resetAt: ISO,
        },
      ],
    });

    expect(parsed.id).toBe("key-1");
    expect(parsed.allowedModels).toEqual(["gpt-4.1"]);
    expect(parsed.limits).toHaveLength(1);
    expect(parsed.limits[0].limitType).toBe("total_tokens");
  });

  it("defaults limits to empty array when not provided", () => {
    const parsed = ApiKeySchema.parse({
      id: "key-1",
      name: "Service Key",
      keyPrefix: "sk-live",
      allowedModels: null,
      expiresAt: null,
      isActive: true,
      createdAt: ISO,
      lastUsedAt: null,
    });

    expect(parsed.limits).toEqual([]);
  });
});

describe("ApiKeyCreateResponseSchema", () => {
  it("requires plain key field in create response", () => {
    const parsed = ApiKeyCreateResponseSchema.parse({
      id: "key-2",
      name: "New Key",
      keyPrefix: "sk-test",
      key: "sk-test-plaintext",
      allowedModels: null,
      expiresAt: null,
      isActive: true,
      createdAt: ISO,
      lastUsedAt: null,
      limits: [],
    });

    expect(parsed.key).toBe("sk-test-plaintext");
  });
});

describe("ApiKeyUpdateRequestSchema", () => {
  it("accepts partial update payload", () => {
    const parsed = ApiKeyUpdateRequestSchema.parse({
      name: "Updated Key",
      allowedModels: ["gpt-4.1-mini"],
      weeklyTokenLimit: 50000,
      expiresAt: ISO,
      isActive: false,
    });

    expect(parsed.name).toBe("Updated Key");
    expect(parsed.isActive).toBe(false);
  });

  it("rejects invalid weeklyTokenLimit", () => {
    const result = ApiKeyUpdateRequestSchema.safeParse({
      weeklyTokenLimit: 0,
    });

    expect(result.success).toBe(false);
  });

  it("accepts limits array", () => {
    const parsed = ApiKeyUpdateRequestSchema.parse({
      limits: [
        { limitType: "cost_usd", limitWindow: "daily", maxValue: 500000 },
      ],
    });

    expect(parsed.limits).toHaveLength(1);
    expect(parsed.limits![0].limitType).toBe("cost_usd");
  });

  it("accepts resetUsage flag", () => {
    const parsed = ApiKeyUpdateRequestSchema.parse({
      resetUsage: true,
    });

    expect(parsed.resetUsage).toBe(true);
  });
});

describe("LimitRuleCreateSchema", () => {
  it("parses valid limit rule", () => {
    const parsed = LimitRuleCreateSchema.parse({
      limitType: "total_tokens",
      limitWindow: "weekly",
      maxValue: 1000000,
    });

    expect(parsed.limitType).toBe("total_tokens");
    expect(parsed.maxValue).toBe(1000000);
  });

  it("rejects invalid limit type", () => {
    const result = LimitRuleCreateSchema.safeParse({
      limitType: "invalid",
      limitWindow: "weekly",
      maxValue: 100,
    });
    expect(result.success).toBe(false);
  });

  it("rejects non-positive maxValue", () => {
    const result = LimitRuleCreateSchema.safeParse({
      limitType: "total_tokens",
      limitWindow: "weekly",
      maxValue: 0,
    });
    expect(result.success).toBe(false);
  });
});
