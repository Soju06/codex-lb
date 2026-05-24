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
      dashboardSessionTtlSeconds: 43200,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
      limitWarmupEnabled: false,
      limitWarmupWindows: "both",
      limitWarmupModel: "auto",
      limitWarmupPrompt: "Say OK.",
      limitWarmupCooldownSeconds: 3600,
      limitWarmupMinAvailablePercent: 100,
    });

    expect(parsed.stickyThreadsEnabled).toBe(true);
    expect(parsed.upstreamStreamTransport).toBe("default");
    expect(parsed.routingStrategy).toBe("round_robin");
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(300);
    expect(parsed.dashboardSessionTtlSeconds).toBe(43200);
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(true);
    expect(parsed.limitWarmupEnabled).toBe(false);
    expect(parsed.limitWarmupWindows).toBe("both");
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
    expect(parsed.limitWarmupEnabled).toBe(false);
    expect(parsed.limitWarmupWindows).toBe("both");
    expect(parsed.limitWarmupModel).toBe("auto");
    expect(parsed.limitWarmupPrompt).toBe("Say OK.");
    expect(parsed.limitWarmupCooldownSeconds).toBe(3600);
    expect(parsed.limitWarmupMinAvailablePercent).toBe(100);
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
      dashboardSessionTtlSeconds: 7200,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      apiKeyAuthEnabled: false,
      limitWarmupEnabled: true,
      limitWarmupWindows: "primary",
      limitWarmupModel: "gpt-5.1-codex-mini",
      limitWarmupPrompt: "Say OK.",
      limitWarmupCooldownSeconds: 7200,
      limitWarmupMinAvailablePercent: 99,
    });

    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(120);
    expect(parsed.dashboardSessionTtlSeconds).toBe(7200);
    expect(parsed.upstreamStreamTransport).toBe("websocket");
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.routingStrategy).toBe("usage_weighted");
    expect(parsed.totpRequiredOnLogin).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(false);
    expect(parsed.limitWarmupEnabled).toBe(true);
    expect(parsed.limitWarmupWindows).toBe("primary");
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

  it("matches backend limit warm-up model and prompt length bounds", () => {
    expect(
      SettingsUpdateRequestSchema.safeParse({
        stickyThreadsEnabled: false,
        preferEarlierResetAccounts: true,
        limitWarmupModel: "m".repeat(129),
      }).success,
    ).toBe(false);
    expect(
      SettingsUpdateRequestSchema.safeParse({
        stickyThreadsEnabled: false,
        preferEarlierResetAccounts: true,
        limitWarmupPrompt: "p".repeat(513),
      }).success,
    ).toBe(false);
  });
});
