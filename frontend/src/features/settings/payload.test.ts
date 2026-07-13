import { describe, expect, it } from "vitest";

import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { DashboardSettingsSchema } from "@/features/settings/schemas";

describe("buildSettingsUpdateRequest", () => {
  it("carries the loaded settings version as expectedVersion for CAS", () => {
    const settings = DashboardSettingsSchema.parse({
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
      version: 7,
    });

    const payload = buildSettingsUpdateRequest(settings, { apiKeyAuthEnabled: false });

    expect(payload.expectedVersion).toBe(7);
    expect(payload.apiKeyAuthEnabled).toBe(false);
  });

  it("omits expectedVersion when the loaded settings carry no version", () => {
    const settings = DashboardSettingsSchema.parse({
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

    const payload = buildSettingsUpdateRequest(settings, { apiKeyAuthEnabled: false });

    expect("expectedVersion" in payload).toBe(false);
  });

  it("does not persist split sticky thresholds synthesized from legacy settings", () => {
    const settings = DashboardSettingsSchema.parse({
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

    const payload = buildSettingsUpdateRequest(settings, { dashboardSessionTtlSeconds: 7200 });

    expect(payload.dashboardSessionTtlSeconds).toBe(7200);
    expect(payload.stickyReallocationBudgetThresholdPct).toBe(95);
    expect(payload.stickyReallocationPrimaryBudgetThresholdPct).toBeUndefined();
    expect(payload.stickyReallocationSecondaryBudgetThresholdPct).toBeUndefined();
  });

  it("does not persist sticky threshold defaults synthesized from older settings", () => {
    const settings = DashboardSettingsSchema.parse({
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

    const payload = buildSettingsUpdateRequest(settings, { dashboardSessionTtlSeconds: 7200 });

    expect(payload.dashboardSessionTtlSeconds).toBe(7200);
    expect(payload.stickyReallocationBudgetThresholdPct).toBeUndefined();
    expect(payload.stickyReallocationPrimaryBudgetThresholdPct).toBeUndefined();
    expect(payload.stickyReallocationSecondaryBudgetThresholdPct).toBeUndefined();
  });

  it("does not persist a legacy threshold synthesized from split settings", () => {
    const settings = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: false,
      routingStrategy: "round_robin",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      stickyReallocationPrimaryBudgetThresholdPct: 90,
      stickyReallocationSecondaryBudgetThresholdPct: 100,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    const payload = buildSettingsUpdateRequest(settings, { dashboardSessionTtlSeconds: 7200 });

    expect(payload.stickyReallocationBudgetThresholdPct).toBeUndefined();
    expect(payload.stickyReallocationPrimaryBudgetThresholdPct).toBe(90);
    expect(payload.stickyReallocationSecondaryBudgetThresholdPct).toBe(100);
  });

  it("persists split sticky thresholds that came from the backend", () => {
    const settings = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: false,
      routingStrategy: "round_robin",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      stickyReallocationBudgetThresholdPct: 95,
      stickyReallocationPrimaryBudgetThresholdPct: 90,
      stickyReallocationSecondaryBudgetThresholdPct: 100,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    const payload = buildSettingsUpdateRequest(settings, { dashboardSessionTtlSeconds: 7200 });

    expect(payload.stickyReallocationPrimaryBudgetThresholdPct).toBe(90);
    expect(payload.stickyReallocationSecondaryBudgetThresholdPct).toBe(100);
  });

  it("keeps the legacy sticky threshold aligned with primary edits", () => {
    const settings = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: false,
      routingStrategy: "round_robin",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      stickyReallocationBudgetThresholdPct: 95,
      stickyReallocationPrimaryBudgetThresholdPct: 95,
      stickyReallocationSecondaryBudgetThresholdPct: 100,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
      limitWarmupStaggeredIdleEnabled: true,
    });

    const payload = buildSettingsUpdateRequest(settings, {
      stickyReallocationPrimaryBudgetThresholdPct: 80,
    });

    expect(payload.limitWarmupStaggeredIdleEnabled).toBe(true);
    expect(payload.stickyReallocationBudgetThresholdPct).toBe(80);
    expect(payload.stickyReallocationPrimaryBudgetThresholdPct).toBe(80);
    expect(payload.stickyReallocationSecondaryBudgetThresholdPct).toBe(100);
  });

  it("includes limit warm-up exhausted threshold updates", () => {
    const settings = DashboardSettingsSchema.parse({
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
      limitWarmupExhaustedThresholdPercent: 99,
      limitWarmupIdleThresholdPercent: 1,
    });

    const payload = buildSettingsUpdateRequest(settings, {
      limitWarmupExhaustedThresholdPercent: 98.5,
      limitWarmupIdleThresholdPercent: 2.5,
    });

    expect(payload.limitWarmupExhaustedThresholdPercent).toBe(98.5);
    expect(payload.limitWarmupIdleThresholdPercent).toBe(2.5);
  });

  it("does not materialize inherited account capacity limits on unrelated updates", () => {
    const settings = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: false,
      routingStrategy: "round_robin",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      proxyAccountResponseCreateLimit: 0,
      proxyAccountStreamLimit: 12,
      proxyAccountStreamRecoveryReserve: 2,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    const payload = buildSettingsUpdateRequest(settings, { warmupModel: "gpt-5.6-sol" });

    expect(payload.warmupModel).toBe("gpt-5.6-sol");
    expect(payload.proxyAccountResponseCreateLimit).toBeUndefined();
    expect(payload.proxyAccountStreamLimit).toBeUndefined();
    expect(payload.proxyAccountStreamRecoveryReserve).toBeUndefined();
  });

  it("includes all account capacity limits when they are explicitly edited", () => {
    const settings = DashboardSettingsSchema.parse({
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

    const payload = buildSettingsUpdateRequest(settings, {
      proxyAccountResponseCreateLimit: 0,
      proxyAccountStreamLimit: 12,
      proxyAccountStreamRecoveryReserve: 2,
    });

    expect(payload).toMatchObject({
      proxyAccountResponseCreateLimit: 0,
      proxyAccountStreamLimit: 12,
      proxyAccountStreamRecoveryReserve: 2,
    });
  });
});
