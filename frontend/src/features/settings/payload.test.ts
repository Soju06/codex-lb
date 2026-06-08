import { describe, expect, it } from "vitest";

import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { DashboardSettingsSchema } from "@/features/settings/schemas";

describe("buildSettingsUpdateRequest", () => {
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
    });

    const payload = buildSettingsUpdateRequest(settings, {
      stickyReallocationPrimaryBudgetThresholdPct: 80,
    });

    expect(payload.stickyReallocationBudgetThresholdPct).toBe(80);
    expect(payload.stickyReallocationPrimaryBudgetThresholdPct).toBe(80);
    expect(payload.stickyReallocationSecondaryBudgetThresholdPct).toBe(100);
  });

  it("preserves nonempty manual account priority when saving unrelated settings", () => {
    const settings = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: false,
      routingStrategy: "ordered_fallback",
      manualAccountPriorityIds: ["acc-two", "acc-one"],
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    const payload = buildSettingsUpdateRequest(settings, { dashboardSessionTtlSeconds: 7200 });

    expect(payload.manualAccountPriorityIds).toEqual(["acc-two", "acc-one"]);
  });
});
