import { describe, expect, it } from "vitest";

import {
  ApiKeyCreateResponseSchema,
  ApiKeySchema,
  ApiKeyUpdateRequestSchema,
} from "@/features/api-keys/schemas";

const ISO = "2026-01-01T00:00:00+00:00";

describe("ApiKeySchema", () => {
  it("parses api key entity payload", () => {
    const parsed = ApiKeySchema.parse({
      id: "key-1",
      name: "Service Key",
      keyPrefix: "sk-live",
      allowedModels: ["gpt-4.1"],
      weeklyTokenLimit: 100000,
      weeklyTokensUsed: 1200,
      weeklyResetAt: ISO,
      expiresAt: null,
      isActive: true,
      createdAt: ISO,
      lastUsedAt: ISO,
    });

    expect(parsed.id).toBe("key-1");
    expect(parsed.allowedModels).toEqual(["gpt-4.1"]);
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
      weeklyTokenLimit: null,
      weeklyTokensUsed: 0,
      weeklyResetAt: ISO,
      expiresAt: null,
      isActive: true,
      createdAt: ISO,
      lastUsedAt: null,
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
});
