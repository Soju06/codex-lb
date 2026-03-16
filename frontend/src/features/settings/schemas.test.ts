import { describe, expect, it } from "vitest";

import {
  DashboardSettingsSchema,
  SettingsUpdateRequestSchema,
} from "@/features/settings/schemas";

describe("DashboardSettingsSchema", () => {
  it("parses settings payload", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: false,
      routingStrategy: "round_robin",
      openaiCacheAffinityMaxAgeSeconds: 300,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    expect(parsed.stickyThreadsEnabled).toBe(true);
    expect(parsed.upstreamStreamTransport).toBe("default");
    expect(parsed.routingStrategy).toBe("round_robin");
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(300);
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(true);
  });

  it("parses legacy settings payload and applies defaults for missing routing fields", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      preferEarlierResetAccounts: false,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    expect(parsed.upstreamStreamTransport).toBe("default");
    expect(parsed.routingStrategy).toBe("usage_weighted");
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(300);
  });
});

describe("SettingsUpdateRequestSchema", () => {
  it("accepts required fields and optional updates", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "websocket",
      preferEarlierResetAccounts: true,
      routingStrategy: "usage_weighted",
      openaiCacheAffinityMaxAgeSeconds: 120,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      apiKeyAuthEnabled: false,
    });

    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(120);
    expect(parsed.upstreamStreamTransport).toBe("websocket");
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.routingStrategy).toBe("usage_weighted");
    expect(parsed.totpRequiredOnLogin).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(false);
  });

  it("accepts payload without optional fields", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
    });

    expect(parsed.upstreamStreamTransport).toBeUndefined();
    expect(parsed.importWithoutOverwrite).toBeUndefined();
    expect(parsed.totpRequiredOnLogin).toBeUndefined();
    expect(parsed.apiKeyAuthEnabled).toBeUndefined();
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBeUndefined();
  });

  it("rejects invalid types", () => {
    const result = SettingsUpdateRequestSchema.safeParse({
      stickyThreadsEnabled: "yes",
      preferEarlierResetAccounts: true,
    });

    expect(result.success).toBe(false);
  });
});
