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
      preferEarlierResetWindow: "secondary",
      routingStrategy: "round_robin",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    expect(parsed.stickyThreadsEnabled).toBe(true);
    expect(parsed.upstreamStreamTransport).toBe("default");
    expect(parsed.preferEarlierResetWindow).toBe("secondary");
    expect(parsed.routingStrategy).toBe("round_robin");
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(300);
    expect(parsed.dashboardSessionTtlSeconds).toBe(43200);
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(true);
  });
});

describe("SettingsUpdateRequestSchema", () => {
  it("accepts required fields and optional updates", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "websocket",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      routingStrategy: "usage_weighted",
      openaiCacheAffinityMaxAgeSeconds: 120,
      dashboardSessionTtlSeconds: 7200,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      apiKeyAuthEnabled: false,
    });

    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(120);
    expect(parsed.dashboardSessionTtlSeconds).toBe(7200);
    expect(parsed.upstreamStreamTransport).toBe("websocket");
    expect(parsed.preferEarlierResetWindow).toBe("secondary");
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.routingStrategy).toBe("usage_weighted");
    expect(parsed.totpRequiredOnLogin).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(false);
  });

  it("accepts long session lifetimes above 30 days", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      dashboardSessionTtlSeconds: 31536000,
    });

    expect(parsed.dashboardSessionTtlSeconds).toBe(31536000);
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
    expect(parsed.dashboardSessionTtlSeconds).toBeUndefined();
  });

  it("rejects invalid types", () => {
    const result = SettingsUpdateRequestSchema.safeParse({
      stickyThreadsEnabled: "yes",
      preferEarlierResetAccounts: true,
    });

    expect(result.success).toBe(false);
  });
});
