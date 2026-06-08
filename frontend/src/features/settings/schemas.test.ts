import { describe, expect, it } from "vitest";

import {
  DashboardSettingsSchema,
  SettingsUpdateRequestSchema,
  UpstreamProxyAdminSchema,
} from "@/features/settings/schemas";

describe("DashboardSettingsSchema", () => {
  it("parses settings payload", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      upstreamProxyRoutingEnabled: true,
      upstreamProxyDefaultPoolId: "pool_1",
      preferEarlierResetAccounts: false,
      routingStrategy: "relative_availability",
      preferEarlierResetWindow: "secondary",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      singleAccountId: "acc-1",
      manualAccountPriorityIds: ["acc-2", "acc-1"],
      weeklyPaceWorkingDays: "0,1,2,3,4",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      stickyReallocationBudgetThresholdPct: 95,
      stickyReallocationPrimaryBudgetThresholdPct: 90,
      stickyReallocationSecondaryBudgetThresholdPct: 100,
      warmupModel: "gpt-5.4-mini",
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
    expect(parsed.upstreamProxyRoutingEnabled).toBe(true);
    expect(parsed.upstreamProxyDefaultPoolId).toBe("pool_1");
    expect(parsed.routingStrategy).toBe("relative_availability");
    expect(parsed.preferEarlierResetWindow).toBe("secondary");
    expect(parsed.relativeAvailabilityPower).toBe(2);
    expect(parsed.relativeAvailabilityTopK).toBe(5);
    expect(parsed.singleAccountId).toBe("acc-1");
    expect(parsed.manualAccountPriorityIds).toEqual(["acc-2", "acc-1"]);
    expect(parsed.weeklyPaceWorkingDays).toBe("0,1,2,3,4");
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(300);
    expect(parsed.dashboardSessionTtlSeconds).toBe(43200);
    expect(parsed.stickyReallocationPrimaryBudgetThresholdPct).toBe(90);
    expect(parsed.stickyReallocationSecondaryBudgetThresholdPct).toBe(100);
    expect(parsed.warmupModel).toBe("gpt-5.4-mini");
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
      stickyReallocationBudgetThresholdPct: 95,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    expect(parsed.upstreamStreamTransport).toBe("default");
    expect(parsed.upstreamProxyRoutingEnabled).toBe(false);
    expect(parsed.upstreamProxyDefaultPoolId).toBeNull();
    expect(parsed.routingStrategy).toBe("usage_weighted");
    expect(parsed.singleAccountId).toBeNull();
    expect(parsed.manualAccountPriorityIds).toEqual([]);
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(300);
    expect(parsed.limitWarmupEnabled).toBe(false);
    expect(parsed.limitWarmupWindows).toBe("both");
    expect(parsed.limitWarmupModel).toBe("auto");
    expect(parsed.limitWarmupPrompt).toBe("Say OK.");
    expect(parsed.limitWarmupCooldownSeconds).toBe(3600);
    expect(parsed.limitWarmupMinAvailablePercent).toBe(100);
    expect(parsed.weeklyPaceWorkingDays).toBe("0,1,2,3,4,5,6");
    expect(parsed.stickyReallocationPrimaryBudgetThresholdPct).toBe(95);
    expect(parsed.stickyReallocationSecondaryBudgetThresholdPct).toBe(95);
  });

  it("falls back to the legacy sticky threshold during mixed-version rollout", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: false,
      routingStrategy: "round_robin",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      stickyReallocationBudgetThresholdPct: 95,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    expect(parsed.stickyReallocationPrimaryBudgetThresholdPct).toBe(95);
    expect(parsed.stickyReallocationSecondaryBudgetThresholdPct).toBe(95);
  });

  it("uses local defaults when mixed-version settings omit sticky thresholds", () => {
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
    });

    expect(parsed.stickyReallocationBudgetThresholdPct).toBe(95);
    expect(parsed.stickyReallocationPrimaryBudgetThresholdPct).toBe(95);
    expect(parsed.stickyReallocationSecondaryBudgetThresholdPct).toBe(100);
  });
});

describe("SettingsUpdateRequestSchema", () => {
  it("accepts required fields and optional updates", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "websocket",
      upstreamProxyRoutingEnabled: true,
      upstreamProxyDefaultPoolId: null,
      preferEarlierResetAccounts: true,
      routingStrategy: "relative_availability",
      preferEarlierResetWindow: "secondary",
      relativeAvailabilityPower: 1.5,
      relativeAvailabilityTopK: 7,
      singleAccountId: "acc-1",
      manualAccountPriorityIds: ["acc-2", "acc-1"],
      weeklyPaceWorkingDays: "0,1,2,3,4",
      openaiCacheAffinityMaxAgeSeconds: 120,
      dashboardSessionTtlSeconds: 7200,
      stickyReallocationBudgetThresholdPct: 95,
      stickyReallocationPrimaryBudgetThresholdPct: 90,
      stickyReallocationSecondaryBudgetThresholdPct: 100,
      warmupModel: " gpt-5.4-nano ",
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
    expect(parsed.stickyReallocationPrimaryBudgetThresholdPct).toBe(90);
    expect(parsed.stickyReallocationSecondaryBudgetThresholdPct).toBe(100);
    expect(parsed.warmupModel).toBe("gpt-5.4-nano");
    expect(parsed.upstreamStreamTransport).toBe("websocket");
    expect(parsed.preferEarlierResetWindow).toBe("secondary");
    expect(parsed.upstreamProxyRoutingEnabled).toBe(true);
    expect(parsed.upstreamProxyDefaultPoolId).toBeNull();
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.routingStrategy).toBe("relative_availability");
    expect(parsed.relativeAvailabilityPower).toBe(1.5);
    expect(parsed.relativeAvailabilityTopK).toBe(7);
    expect(parsed.singleAccountId).toBe("acc-1");
    expect(parsed.manualAccountPriorityIds).toEqual(["acc-2", "acc-1"]);
    expect(parsed.weeklyPaceWorkingDays).toBe("0,1,2,3,4");
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
    expect(parsed.upstreamProxyRoutingEnabled).toBeUndefined();
    expect(parsed.upstreamProxyDefaultPoolId).toBeUndefined();
    expect(parsed.importWithoutOverwrite).toBeUndefined();
    expect(parsed.totpRequiredOnLogin).toBeUndefined();
    expect(parsed.apiKeyAuthEnabled).toBeUndefined();
    expect(parsed.relativeAvailabilityPower).toBeUndefined();
    expect(parsed.relativeAvailabilityTopK).toBeUndefined();
    expect(parsed.singleAccountId).toBeUndefined();
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBeUndefined();
    expect(parsed.dashboardSessionTtlSeconds).toBeUndefined();
    expect(parsed.warmupModel).toBeUndefined();
    expect(parsed.weeklyPaceWorkingDays).toBeUndefined();
  });

  it("rejects invalid types", () => {
    const result = SettingsUpdateRequestSchema.safeParse({
      stickyThreadsEnabled: "yes",
      preferEarlierResetAccounts: true,
    });

    expect(result.success).toBe(false);
  });

  it("accepts fill_first as a valid routing strategy", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      routingStrategy: "fill_first",
    });

    expect(parsed.routingStrategy).toBe("fill_first");
  });

  it("accepts ordered_fallback as a valid routing strategy", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      routingStrategy: "ordered_fallback",
      manualAccountPriorityIds: ["acc-2", "acc-1"],
    });

    expect(parsed.routingStrategy).toBe("ordered_fallback");
    expect(parsed.manualAccountPriorityIds).toEqual(["acc-2", "acc-1"]);
  });

  it("rejects unknown routing strategies", () => {
    const result = SettingsUpdateRequestSchema.safeParse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      routingStrategy: "fill_last",
    });

    expect(result.success).toBe(false);
  });

  it("rejects invalid weekly pace working days", () => {
    expect(
      SettingsUpdateRequestSchema.safeParse({
        stickyThreadsEnabled: false,
        preferEarlierResetAccounts: true,
        weeklyPaceWorkingDays: "0,1,7",
      }).success,
    ).toBe(false);
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

describe("UpstreamProxyAdminSchema", () => {
  it("parses upstream proxy admin state", () => {
    const parsed = UpstreamProxyAdminSchema.parse({
      routingEnabled: true,
      defaultPoolId: "pool_1",
      endpoints: [
        {
          id: "ep_1",
          name: "Proxy A",
          scheme: "http",
          host: "proxy.test",
          port: 8080,
          username: null,
          isActive: true,
        },
      ],
      pools: [
        {
          id: "pool_1",
          name: "Pool A",
          isActive: true,
          endpointIds: ["ep_1"],
        },
      ],
      bindings: [{ accountId: "acc_1", poolId: "pool_1", isActive: true }],
    });

    expect(parsed.routingEnabled).toBe(true);
    expect(parsed.endpoints[0]?.host).toBe("proxy.test");
    expect(parsed.pools[0]?.endpointIds).toEqual(["ep_1"]);
    expect(parsed.bindings[0]?.accountId).toBe("acc_1");
  });
});
