import { describe, expect, it } from "vitest";

import {
  DashboardSettingsSchema,
  SettingsUpdateRequestSchema,
  SubscriptionOverflowPreflightSchema,
  TelemetryConsentSchema,
  TelemetryDaySchema,
  TelemetrySnapshotEnvelopeSchema,
  UpstreamProxyAdminSchema,
} from "@/features/settings/schemas";
import {
  createDashboardSettings,
  createTelemetryDay,
  createTelemetryPreview,
  createTelemetrySnapshotEnvelope,
} from "@/test/mocks/factories";

describe("DashboardSettingsSchema", () => {
  it("parses settings payload", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "auto",
      upstreamProxyRoutingEnabled: true,
      upstreamProxyDefaultPoolId: "pool_1",
      preferEarlierResetAccounts: false,
      routingStrategy: "relative_availability",
      preferEarlierResetWindow: "secondary",
      showResetCreditBadges: false,
      autoRedeemResetCreditsBeforeExpiry: true,
      showResetCreditExpiryBadge: false,
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      singleAccountId: "acc-1",
      proxyAccountResponseCreateLimit: 6,
      proxyAccountStreamLimit: 12,
      proxyAccountStreamRecoveryReserve: 2,
      proxyApiKeyFairShareCongestionThresholdPct: 80,
      weeklyPaceWorkingDays: "0,1,2,3,4",
      weeklyPaceSmoothingMinutes: 60,
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      stickyReallocationBudgetThresholdPct: 95,
      stickyReallocationPrimaryBudgetThresholdPct: 90,
      stickyReallocationSecondaryBudgetThresholdPct: 100,
      warmupModel: "gpt-5.4-mini",
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      guestAccessEnabled: true,
      guestPasswordConfigured: false,
      apiKeyAuthEnabled: true,
      hideUpstreamQuotaFromApiKeys: false,
      limitWarmupEnabled: false,
      limitWarmupWindows: "both",
      limitWarmupModel: "auto",
      limitWarmupPrompt: "Say OK.",
      limitWarmupCooldownSeconds: 3600,
      limitWarmupExhaustedThresholdPercent: 99,
      limitWarmupIdleThresholdPercent: 1,
      limitWarmupMinAvailablePercent: 100,
      limitWarmupStaggeredIdleEnabled: true,
    });

    expect(parsed.stickyThreadsEnabled).toBe(true);
    expect(parsed.upstreamStreamTransport).toBe("auto");
    expect(parsed.upstreamProxyRoutingEnabled).toBe(true);
    expect(parsed.upstreamProxyDefaultPoolId).toBe("pool_1");
    expect(parsed.routingStrategy).toBe("relative_availability");
    expect(parsed.preferEarlierResetWindow).toBe("secondary");
    expect(parsed.showResetCreditBadges).toBe(false);
    expect(parsed.autoRedeemResetCreditsBeforeExpiry).toBe(true);
    expect(parsed.showResetCreditExpiryBadge).toBe(false);
    expect(parsed.relativeAvailabilityPower).toBe(2);
    expect(parsed.relativeAvailabilityTopK).toBe(5);
    expect(parsed.singleAccountId).toBe("acc-1");
    expect(parsed.proxyAccountResponseCreateLimit).toBe(6);
    expect(parsed.proxyAccountResponseCreateLimitOverride).toBeNull();
    expect(parsed.proxyAccountStreamLimit).toBe(12);
    expect(parsed.proxyAccountStreamLimitOverride).toBeNull();
    expect(parsed.proxyAccountStreamRecoveryReserve).toBe(2);
    expect(parsed.proxyAccountStreamRecoveryReserveOverride).toBeNull();
    expect(parsed.proxyApiKeyFairShareCongestionThresholdPct).toBe(80);
    expect(parsed.proxyApiKeyFairShareCongestionThresholdPctOverride).toBeNull();
    expect(parsed.weeklyPaceWorkingDays).toBe("0,1,2,3,4");
    expect(parsed.weeklyPaceSmoothingMinutes).toBe(60);
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(300);
    expect(parsed.dashboardSessionTtlSeconds).toBe(43200);
    expect(parsed.stickyReallocationPrimaryBudgetThresholdPct).toBe(90);
    expect(parsed.stickyReallocationSecondaryBudgetThresholdPct).toBe(100);
    expect(parsed.warmupModel).toBe("gpt-5.4-mini");
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.guestAccessEnabled).toBe(true);
    expect(parsed.guestPasswordConfigured).toBe(false);
    expect(parsed.apiKeyAuthEnabled).toBe(true);
    expect(parsed.hideUpstreamQuotaFromApiKeys).toBe(false);
    expect(parsed.limitWarmupEnabled).toBe(false);
    expect(parsed.limitWarmupWindows).toBe("both");
    expect(parsed.limitWarmupStaggeredIdleEnabled).toBe(true);
  });

  it("reads an inherited in-flight penalty above the dashboard write cap", () => {
    // The environment field has no upper bound; only dashboard writes cap at 100.
    const parsed = DashboardSettingsSchema.parse({
      ...createDashboardSettings(),
      proxyAccountInflightPenaltyPct: 150,
      provenance: { proxy_account_inflight_penalty_pct: { source: "env", envValue: 150, default: 2.5 } },
    });
    expect(parsed.proxyAccountInflightPenaltyPct).toBe(150);
    expect(parsed.provenance?.proxy_account_inflight_penalty_pct?.source).toBe("env");
    expect(SettingsUpdateRequestSchema.safeParse({ proxyAccountInflightPenaltyPct: 150 }).success).toBe(false);
    expect(SettingsUpdateRequestSchema.safeParse({ proxyAccountInflightPenaltyPct: null }).success).toBe(true);
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
      hideUpstreamQuotaFromApiKeys: false,
    });

    expect(parsed.upstreamStreamTransport).toBe("auto");
    expect(parsed.upstreamProxyRoutingEnabled).toBe(false);
    expect(parsed.upstreamProxyDefaultPoolId).toBeNull();
    expect(parsed.routingStrategy).toBe("usage_weighted");
    expect(parsed.singleAccountId).toBeNull();
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(300);
    expect(parsed.dashboardSessionTtlSeconds).toBe(31536000);
    expect(parsed.proxyAccountResponseCreateLimit).toBe(4);
    expect(parsed.proxyAccountStreamLimit).toBe(8);
    expect(parsed.proxyAccountStreamRecoveryReserve).toBe(1);
    expect(parsed.proxyApiKeyFairShareCongestionThresholdPct).toBe(0);
    expect(parsed.limitWarmupEnabled).toBe(false);
    expect(parsed.limitWarmupWindows).toBe("both");
    expect(parsed.limitWarmupModel).toBe("auto");
    expect(parsed.limitWarmupPrompt).toBe("Say OK.");
    expect(parsed.limitWarmupCooldownSeconds).toBe(3600);
    expect(parsed.limitWarmupExhaustedThresholdPercent).toBe(99);
    expect(parsed.limitWarmupMinAvailablePercent).toBe(100);
    expect(parsed.weeklyPaceWorkingDays).toBe("0,1,2,3,4,5,6");
    expect(parsed.weeklyPaceSmoothingMinutes).toBe(30);
    expect(parsed.limitWarmupStaggeredIdleEnabled).toBe(false);
    expect(parsed.showResetCreditBadges).toBe(true);
    expect(parsed.autoRedeemResetCreditsBeforeExpiry).toBe(false);
    expect(parsed.showResetCreditExpiryBadge).toBe(true);
    expect(parsed.stickyReallocationPrimaryBudgetThresholdPct).toBe(95);
    expect(parsed.stickyReallocationSecondaryBudgetThresholdPct).toBe(95);
    expect(parsed.guestAccessEnabled).toBe(false);
    expect(parsed.guestPasswordConfigured).toBe(false);
  });

  it("falls back to the legacy sticky threshold during mixed-version rollout", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "auto",
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
      upstreamStreamTransport: "auto",
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
    expect(parsed.guestAccessEnabled).toBe(false);
    expect(parsed.guestPasswordConfigured).toBe(false);
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
      showResetCreditBadges: false,
      autoRedeemResetCreditsBeforeExpiry: true,
      showResetCreditExpiryBadge: false,
      relativeAvailabilityPower: 1.5,
      relativeAvailabilityTopK: 7,
      singleAccountId: "acc-1",
      proxyAccountResponseCreateLimit: 6,
      proxyAccountStreamLimit: 12,
      proxyAccountStreamRecoveryReserve: 2,
      proxyApiKeyFairShareCongestionThresholdPct: 80,
      weeklyPaceWorkingDays: "0,1,2,3,4",
      weeklyPaceSmoothingMinutes: 120,
      openaiCacheAffinityMaxAgeSeconds: 120,
      dashboardSessionTtlSeconds: 7200,
      stickyReallocationBudgetThresholdPct: 95,
      stickyReallocationPrimaryBudgetThresholdPct: 90,
      stickyReallocationSecondaryBudgetThresholdPct: 100,
      warmupModel: " gpt-5.4-nano ",
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      apiKeyAuthEnabled: false,
      hideUpstreamQuotaFromApiKeys: true,
      limitWarmupEnabled: true,
      limitWarmupWindows: "primary",
      limitWarmupModel: "gpt-5.1-codex-mini",
      limitWarmupPrompt: "Say OK.",
      limitWarmupCooldownSeconds: 7200,
      limitWarmupExhaustedThresholdPercent: 98.5,
      limitWarmupMinAvailablePercent: 99,
      limitWarmupStaggeredIdleEnabled: true,
    });

    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBe(120);
    expect(parsed.dashboardSessionTtlSeconds).toBe(7200);
    expect(parsed.stickyReallocationPrimaryBudgetThresholdPct).toBe(90);
    expect(parsed.stickyReallocationSecondaryBudgetThresholdPct).toBe(100);
    expect(parsed.warmupModel).toBe("gpt-5.4-nano");
    expect(parsed.upstreamStreamTransport).toBe("websocket");
    expect(parsed.preferEarlierResetWindow).toBe("secondary");
    expect(parsed.showResetCreditBadges).toBe(false);
    expect(parsed.autoRedeemResetCreditsBeforeExpiry).toBe(true);
    expect(parsed.showResetCreditExpiryBadge).toBe(false);
    expect(parsed.upstreamProxyRoutingEnabled).toBe(true);
    expect(parsed.upstreamProxyDefaultPoolId).toBeNull();
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.routingStrategy).toBe("relative_availability");
    expect(parsed.relativeAvailabilityPower).toBe(1.5);
    expect(parsed.relativeAvailabilityTopK).toBe(7);
    expect(parsed.singleAccountId).toBe("acc-1");
    expect(parsed.proxyAccountResponseCreateLimit).toBe(6);
    expect(parsed.proxyAccountStreamLimit).toBe(12);
    expect(parsed.proxyAccountStreamRecoveryReserve).toBe(2);
    expect(parsed.proxyApiKeyFairShareCongestionThresholdPct).toBe(80);
    expect(parsed.weeklyPaceWorkingDays).toBe("0,1,2,3,4");
    expect(parsed.weeklyPaceSmoothingMinutes).toBe(120);
    expect(parsed.totpRequiredOnLogin).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(false);
    expect(parsed.hideUpstreamQuotaFromApiKeys).toBe(true);
    expect(parsed.limitWarmupEnabled).toBe(true);
    expect(parsed.limitWarmupWindows).toBe("primary");
    expect(parsed.limitWarmupExhaustedThresholdPercent).toBe(98.5);
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
    expect(parsed.showResetCreditBadges).toBeUndefined();
    expect(parsed.autoRedeemResetCreditsBeforeExpiry).toBeUndefined();
    expect(parsed.showResetCreditExpiryBadge).toBeUndefined();
    expect(parsed.hideUpstreamQuotaFromApiKeys).toBeUndefined();
    expect(parsed.relativeAvailabilityPower).toBeUndefined();
    expect(parsed.relativeAvailabilityTopK).toBeUndefined();
    expect(parsed.singleAccountId).toBeUndefined();
    expect(parsed.openaiCacheAffinityMaxAgeSeconds).toBeUndefined();
    expect(parsed.dashboardSessionTtlSeconds).toBeUndefined();
    expect(parsed.proxyAccountResponseCreateLimit).toBeUndefined();
    expect(parsed.proxyAccountStreamLimit).toBeUndefined();
    expect(parsed.proxyAccountStreamRecoveryReserve).toBeUndefined();
    expect(parsed.proxyApiKeyFairShareCongestionThresholdPct).toBeUndefined();
    expect(parsed.warmupModel).toBeUndefined();
    expect(parsed.weeklyPaceWorkingDays).toBeUndefined();
    expect(parsed.weeklyPaceSmoothingMinutes).toBeUndefined();
  });

  it("rejects invalid types", () => {
    const result = SettingsUpdateRequestSchema.safeParse({
      stickyThreadsEnabled: "yes",
      preferEarlierResetAccounts: true,
    });

    expect(result.success).toBe(false);
  });

  it("rejects negative and fractional account capacity limits", () => {
    for (const payload of [
      { proxyAccountResponseCreateLimit: -1 },
      { proxyAccountStreamLimit: 1.5 },
      { proxyAccountStreamRecoveryReserve: -1 },
    ]) {
      expect(
        SettingsUpdateRequestSchema.safeParse({
          stickyThreadsEnabled: false,
          preferEarlierResetAccounts: true,
          ...payload,
        }).success,
      ).toBe(false);
    }
  });

  it("accepts explicit null capacity override clears", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      proxyAccountResponseCreateLimit: null,
      proxyAccountStreamLimit: null,
      proxyAccountStreamRecoveryReserve: null,
      proxyApiKeyFairShareCongestionThresholdPct: null,
    });

    expect(parsed.proxyAccountResponseCreateLimit).toBeNull();
    expect(parsed.proxyAccountStreamLimit).toBeNull();
    expect(parsed.proxyAccountStreamRecoveryReserve).toBeNull();
    expect(parsed.proxyApiKeyFairShareCongestionThresholdPct).toBeNull();
  });

  it("rejects out-of-range and fractional fair-share congestion thresholds", () => {
    for (const payload of [
      { proxyApiKeyFairShareCongestionThresholdPct: -1 },
      { proxyApiKeyFairShareCongestionThresholdPct: 1.5 },
      { proxyApiKeyFairShareCongestionThresholdPct: 101 },
    ]) {
      expect(
        SettingsUpdateRequestSchema.safeParse({
          stickyThreadsEnabled: false,
          preferEarlierResetAccounts: true,
          ...payload,
        }).success,
      ).toBe(false);
    }
  });

  it("rejects a recovery reserve above a nonzero stream limit", () => {
    expect(
      SettingsUpdateRequestSchema.safeParse({
        stickyThreadsEnabled: false,
        preferEarlierResetAccounts: true,
        proxyAccountStreamLimit: 2,
        proxyAccountStreamRecoveryReserve: 3,
      }).success,
    ).toBe(false);
  });

  it("accepts a legacy inherited reserve above the stream limit in settings responses", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      preferEarlierResetAccounts: true,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      apiKeyAuthEnabled: false,
      proxyAccountStreamLimit: 1,
      proxyAccountStreamRecoveryReserve: 2,
    });

    expect(parsed.proxyAccountStreamLimit).toBe(1);
    expect(parsed.proxyAccountStreamRecoveryReserve).toBe(2);
  });

  it("accepts fill_first as a valid routing strategy", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      routingStrategy: "fill_first",
    });

    expect(parsed.routingStrategy).toBe("fill_first");
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

  it("rejects invalid weekly pace smoothing windows", () => {
    expect(
      SettingsUpdateRequestSchema.safeParse({
        stickyThreadsEnabled: false,
        preferEarlierResetAccounts: true,
        weeklyPaceSmoothingMinutes: 45,
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

  it("matches backend limit warm-up threshold bounds", () => {
    expect(
      SettingsUpdateRequestSchema.safeParse({
        stickyThreadsEnabled: false,
        preferEarlierResetAccounts: true,
        limitWarmupExhaustedThresholdPercent: 0,
      }).success,
    ).toBe(false);
    expect(
      SettingsUpdateRequestSchema.safeParse({
        stickyThreadsEnabled: false,
        preferEarlierResetAccounts: true,
        limitWarmupExhaustedThresholdPercent: 100.1,
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

describe("TelemetrySnapshotEnvelopeSchema", () => {
  it("parses the exact transmitted heartbeat envelope", () => {
    const parsed = TelemetrySnapshotEnvelopeSchema.parse(createTelemetrySnapshotEnvelope());

    expect(parsed.instance_id).toBe("00000000-0000-4000-8000-000000000000");
    expect(parsed.timestamp).toBe("2026-08-06T00:00:00Z");
    expect(parsed.metrics.schema_version).toBe(2);
    expect(parsed.metrics.deploy.method).toBe("docker");
    // Exact account-row counts replaced the v1 pool/plan buckets.
    expect(parsed.metrics.accounts.total).toBe(2);
    expect(parsed.metrics.accounts.per_plan).toEqual({ plus: 2, pro: 0, team: 0, free: 0 });
    expect(parsed.metrics.accounts.per_status).toEqual({ active: 2 });
    expect(parsed.metrics.usage_7d.request_kinds.unknown).toBe(1);
    expect(parsed.metrics.usage_7d.models[0]?.reasoning).toEqual({ high: 0.5, medium: 0.5 });
    expect(parsed.metrics.features.dashboard_auth).toBe(true);
  });

  it("rejects the legacy v1 heartbeat shape", () => {
    // v1 carried bucketed accounts under schema_version 1; each marker alone
    // must fail parsing so a stale backend cannot pass as v2.
    const legacy = structuredClone(createTelemetrySnapshotEnvelope()) as unknown as {
      metrics: Record<string, unknown>;
    };
    legacy.metrics.schema_version = 1;
    legacy.metrics.accounts = {
      pool_bucket: "2-5",
      plan_mix: { plus: "2-5", pro: "0", team: "0", free: "0" },
      workspace_accounts: false,
      routing_policy: "usage_weighted",
      limit_warmup_enabled: false,
      egress_proxy_used: false,
    };
    expect(TelemetrySnapshotEnvelopeSchema.safeParse(legacy).success).toBe(false);

    const versionOnly = structuredClone(createTelemetrySnapshotEnvelope()) as unknown as {
      metrics: Record<string, unknown>;
    };
    versionOnly.metrics.schema_version = 1;
    expect(TelemetrySnapshotEnvelopeSchema.safeParse(versionOnly).success).toBe(false);

    const poolBucketOnly = structuredClone(createTelemetrySnapshotEnvelope());
    (poolBucketOnly.metrics.accounts as Record<string, unknown>).pool_bucket = "2-5";
    expect(TelemetrySnapshotEnvelopeSchema.safeParse(poolBucketOnly).success).toBe(false);
  });

  it.each([
    ["db_size_bucket", ["metrics", "deploy"], "100GB+"],
    ["cost_usd_bucket", ["metrics", "usage_7d"], "1k+"],
    ["avg_output_tokens_bucket", ["metrics", "usage_7d", "models", "0"], "1k+"],
    ["api_keys_bucket", ["metrics", "features"], "many"],
  ] as const)("rejects an undocumented %s value", (field, path, value) => {
    // Buckets are closed sets (openspec/specs/telemetry/context.md); a value
    // outside the documented set is backend drift, not a new bucket.
    const envelope = structuredClone(createTelemetrySnapshotEnvelope());
    let target = envelope as unknown as Record<string, unknown>;
    for (const key of path) {
      target = target[key] as Record<string, unknown>;
    }
    target[field] = value;
    expect(TelemetrySnapshotEnvelopeSchema.safeParse(envelope).success).toBe(false);
  });

  it("rejects unknown extra fields at every object layer so backend drift fails parsing", () => {
    // One path per strict object in the envelope tree; loosening any single
    // layer back to a non-strict schema fails this test.
    const layers: string[][] = [
      [],
      ["metrics"],
      ["metrics", "deploy"],
      ["metrics", "accounts"],
      ["metrics", "usage_7d"],
      ["metrics", "usage_7d", "request_kinds"],
      ["metrics", "usage_7d", "transport_mix"],
      ["metrics", "usage_7d", "service_tier_mix"],
      ["metrics", "usage_7d", "models", "0"],
      ["metrics", "features"],
    ];
    for (const path of layers) {
      const envelope = structuredClone(createTelemetrySnapshotEnvelope());
      let target = envelope as unknown as Record<string, unknown>;
      for (const key of path) {
        target = target[key] as Record<string, unknown>;
      }
      target.drifted_field = true;
      expect(
        TelemetrySnapshotEnvelopeSchema.safeParse(envelope).success,
        `extra field at ${path.join(".") || "envelope root"} must fail parsing`,
      ).toBe(false);
    }
  });

  it("rejects missing required fields so backend drift fails parsing", () => {
    const missingTimestamp = structuredClone(createTelemetrySnapshotEnvelope()) as Record<
      string,
      unknown
    >;
    delete missingTimestamp.timestamp;
    expect(TelemetrySnapshotEnvelopeSchema.safeParse(missingTimestamp).success).toBe(false);

    const missingNested = structuredClone(createTelemetrySnapshotEnvelope());
    delete (missingNested.metrics.usage_7d.request_kinds as Record<string, unknown>).unknown;
    expect(TelemetrySnapshotEnvelopeSchema.safeParse(missingNested).success).toBe(false);
  });
});

describe("TelemetryDaySchema", () => {
  it.each(["latency_ms", "ttft_ms", "tps"] as const)("rejects a mismatched %s histogram count", (metric) => {
    const day = structuredClone(createTelemetryDay());
    day.dimensions.global[metric] = { sample_count: 4, buckets: { "1": 2, "2": 1 } };
    expect(TelemetryDaySchema.safeParse(day).success).toBe(false);
  });

  it.each([
    ["latency_ms", "14"], ["ttft_ms", "14"], ["tps", "11"],
    ["latency_ms", "other"], ["latency_ms", "01"], ["tps", "-1"],
    ["latency_ms", "13\n"], ["tps", " 1"],
  ] as const)("rejects bucket index %s.%s", (metric, index) => {
    const day = structuredClone(createTelemetryDay());
    day.dimensions.models[0][metric] = { sample_count: 1, buckets: { [index]: 1 } };
    expect(TelemetryDaySchema.safeParse(day).success).toBe(false);
  });

  it.each([["latency_ms", "13"], ["ttft_ms", "13"], ["tps", "10"]] as const)(
    "accepts the final valid %s bucket %s and an empty histogram",
    (metric, index) => {
      const day = structuredClone(createTelemetryDay());
      day.dimensions.global[metric] = { sample_count: 3, buckets: { "0": 1, [index]: 2 } };
      day.dimensions.models[0][metric] = { sample_count: 0, buckets: {} };
      expect(TelemetryDaySchema.safeParse(day).success).toBe(true);
    },
  );

  it("parses the completed-day body", () => {
    const parsed = TelemetryDaySchema.parse(createTelemetryDay());

    expect(parsed.schema_version).toBe(2);
    expect(parsed.instance_id).toBe("00000000-0000-4000-8000-000000000000");
    expect(parsed.utc_date).toBe("2026-08-05");
    expect(parsed.dimensions.global.requests).toBe(3);
    expect(parsed.dimensions.global.latency_ms).toEqual({ sample_count: 3, buckets: { "6": 3 } });
    expect(parsed.dimensions.transport.map((entry) => entry.name)).toEqual(["ws", "http_bridge"]);
    expect(parsed.dimensions.request_kinds).toEqual({ responses: 0, chat: 0, images: 0, unknown: 3 });
    expect(parsed.errors.http_status_class).toEqual({ "2xx": 2, "5xx": 1 });
    expect(parsed.errors.outcomes).toEqual({ success: 2, error: 1, cancelled: 0 });
  });

  it("rejects unknown extra fields at every object layer so backend drift fails parsing", () => {
    const layers: string[][] = [
      [],
      ["dimensions"],
      ["dimensions", "request_kinds"],
      ["dimensions", "global"],
      ["dimensions", "global", "latency_ms"],
      ["dimensions", "models", "0"],
      ["errors"],
      ["errors", "outcomes"],
    ];
    for (const path of layers) {
      const day = structuredClone(createTelemetryDay());
      let target = day as unknown as Record<string, unknown>;
      for (const key of path) {
        target = target[key] as Record<string, unknown>;
      }
      target.drifted_field = true;
      expect(
        TelemetryDaySchema.safeParse(day).success,
        `extra field at ${path.join(".") || "day root"} must fail parsing`,
      ).toBe(false);
    }
  });

  it("rejects a cross-dimension cell nested under a dimension entry", () => {
    // The day body carries independent marginals only; a model entry with a
    // per-client breakdown is the re-identification vector the spec forbids.
    const day = structuredClone(createTelemetryDay());
    (day.dimensions.models[0] as unknown as Record<string, unknown>).clients = [
      { name: "codex-cli", requests: 3 },
    ];
    expect(TelemetryDaySchema.safeParse(day).success).toBe(false);
  });

  it("rejects an in-progress day timestamp in place of the completed UTC date", () => {
    const day = structuredClone(createTelemetryDay()) as unknown as Record<string, unknown>;
    day.utc_date = "2026-08-05T12:00:00Z";
    expect(TelemetryDaySchema.safeParse(day).success).toBe(false);
  });
});

describe("TelemetryConsentSchema", () => {
  const decided = { state: "enabled", source: "persisted", active: true, notice_version: 2 };

  it("parses consent with and without the preview bodies", () => {
    const withPreview = TelemetryConsentSchema.parse({
      state: "undecided",
      source: "default",
      active: true,
      notice_version: 2,
      preview: createTelemetryPreview(),
    });
    expect(withPreview.notice_version).toBe(2);
    expect(withPreview.preview?.heartbeat.metrics.schema_version).toBe(2);
    expect(withPreview.preview?.day.utc_date).toBe("2026-08-05");

    const withoutPreview = TelemetryConsentSchema.parse({ ...decided, preview: null });
    expect(withoutPreview.preview).toBeNull();
  });

  it("rejects a preview carrying only one of the two bodies", () => {
    const { heartbeat, day } = createTelemetryPreview();
    for (const preview of [{ heartbeat }, { day }]) {
      expect(
        TelemetryConsentSchema.safeParse({ ...decided, preview }).success,
        `preview with keys ${Object.keys(preview).join(",")} must fail parsing`,
      ).toBe(false);
    }
  });

  it("rejects a bare heartbeat envelope in place of the preview object", () => {
    // v1 attached the envelope directly as `preview`; v2 wraps both bodies.
    expect(
      TelemetryConsentSchema.safeParse({ ...decided, preview: createTelemetrySnapshotEnvelope() })
        .success,
    ).toBe(false);
  });

  it("rejects consent responses that omit the preview field or the notice version", () => {
    expect(TelemetryConsentSchema.safeParse(decided).success).toBe(false);
    expect(
      TelemetryConsentSchema.safeParse({
        state: "enabled",
        source: "persisted",
        active: true,
        preview: null,
      }).success,
    ).toBe(false);
  });
});

describe("retention fields", () => {
  it("parses effective values plus overrides, defaulting for older backends", () => {
    const withValues = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      preferEarlierResetAccounts: true,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      apiKeyAuthEnabled: false,
      requestLogRetentionDays: 90,
      usageHistoryRetentionDays: 45,
      requestLogRetentionOverrideDays: null,
      usageHistoryRetentionOverrideDays: 45,
    });
    expect(withValues.requestLogRetentionDays).toBe(90);
    expect(withValues.usageHistoryRetentionDays).toBe(45);
    expect(withValues.requestLogRetentionOverrideDays).toBeNull();
    expect(withValues.usageHistoryRetentionOverrideDays).toBe(45);

    const withoutValues = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      preferEarlierResetAccounts: true,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      apiKeyAuthEnabled: false,
    });
    expect(withoutValues.requestLogRetentionDays).toBe(0);
    expect(withoutValues.usageHistoryRetentionDays).toBe(0);
    expect(withoutValues.requestLogRetentionOverrideDays).toBeNull();
    expect(withoutValues.usageHistoryRetentionOverrideDays).toBeNull();
  });

  it("accepts 0, floor-or-above, and null (clear) override updates", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      requestLogRetentionOverrideDays: 30,
      usageHistoryRetentionOverrideDays: 0,
    });
    expect(parsed.requestLogRetentionOverrideDays).toBe(30);
    expect(parsed.usageHistoryRetentionOverrideDays).toBe(0);

    const cleared = SettingsUpdateRequestSchema.parse({
      requestLogRetentionOverrideDays: null,
      usageHistoryRetentionOverrideDays: null,
    });
    expect(cleared.requestLogRetentionOverrideDays).toBeNull();
    expect(cleared.usageHistoryRetentionOverrideDays).toBeNull();
  });

  it("rejects override updates between 1 and the safety floor", () => {
    expect(() => SettingsUpdateRequestSchema.parse({ requestLogRetentionOverrideDays: 7 })).toThrow(
      /request_log_retention_override_days must be 0 \(disabled\) or >= 30/,
    );
    expect(() => SettingsUpdateRequestSchema.parse({ usageHistoryRetentionOverrideDays: 44 })).toThrow(
      /usage_history_retention_override_days must be 0 \(disabled\) or >= 45/,
    );
  });

  it("rejects override updates above 3650 days", () => {
    expect(() => SettingsUpdateRequestSchema.parse({ requestLogRetentionOverrideDays: 3651 })).toThrow();
    expect(() => SettingsUpdateRequestSchema.parse({ usageHistoryRetentionOverrideDays: 3651 })).toThrow();
  });
});

describe("subscription overflow fields", () => {
  it("defaults the designation and drain deadline to null for older backends", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "auto",
      preferEarlierResetAccounts: false,
      routingStrategy: "round_robin",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      apiKeyAuthEnabled: false,
    });

    expect(parsed.subscriptionOverflowSourceId).toBeNull();
    expect(parsed.subscriptionOverflowDrainUntil).toBeNull();
    expect(parsed.subscriptionOverflowPinsExpireBy).toBeNull();
  });

  it("round-trips a designation, an ISO drain deadline and the derived pin expiry", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "auto",
      preferEarlierResetAccounts: false,
      routingStrategy: "round_robin",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      importWithoutOverwrite: true,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      apiKeyAuthEnabled: false,
      subscriptionOverflowSourceId: "src_1",
      subscriptionOverflowDrainUntil: "2026-10-07T12:34:56.123456Z",
      subscriptionOverflowPinsExpireBy: "2026-09-15T12:34:56.123456Z",
    });

    expect(parsed.subscriptionOverflowSourceId).toBe("src_1");
    expect(parsed.subscriptionOverflowDrainUntil).toBe("2026-10-07T12:34:56.123456Z");
    expect(parsed.subscriptionOverflowPinsExpireBy).toBe("2026-09-15T12:34:56.123456Z");
  });

  it("accepts the tri-state designation on update requests and rejects the read-only deadlines", () => {
    expect(SettingsUpdateRequestSchema.parse({ subscriptionOverflowSourceId: "src_1" }).subscriptionOverflowSourceId).toBe(
      "src_1",
    );
    expect(SettingsUpdateRequestSchema.parse({ subscriptionOverflowSourceId: null }).subscriptionOverflowSourceId).toBeNull();
    expect(SettingsUpdateRequestSchema.parse({}).subscriptionOverflowSourceId).toBeUndefined();
    expect(
      "subscriptionOverflowDrainUntil" in SettingsUpdateRequestSchema.parse({ subscriptionOverflowDrainUntil: "x" }),
    ).toBe(false);
    expect(
      "subscriptionOverflowPinsExpireBy" in
        SettingsUpdateRequestSchema.parse({ subscriptionOverflowPinsExpireBy: "x" }),
    ).toBe(false);
  });

  it("parses the preflight report", () => {
    const preflight = SubscriptionOverflowPreflightSchema.parse({
      sourceId: "src_1",
      sourceName: "vLLM",
      sourceEnabled: true,
      eligible: false,
      blockers: ["source_responses_unsupported"],
      drainUntil: null,
      servedModels: [
        {
          slug: "gpt-5.4",
          enabled: true,
          neverOverflows: false,
          undeclaredToolTypes: ["shell"],
          supportsVision: false,
          supportsStreaming: true,
          priced: false,
          contextWindowMismatch: { registry: 272000, source: null, maxOutputTokens: null },
          warnings: ["undeclared_tool_types", "context_window_missing"],
        },
      ],
      missingModels: [],
      scopedApiKeyCount: 0,
      livePinCount: 0,
      tombstoneCount: 0,
    });

    expect(preflight.eligible).toBe(false);
    expect(preflight.servedModels[0].neverOverflowsReason).toBeNull();
    expect(preflight.servedModels[0].contextWindowMismatch).toEqual({
      registry: 272000,
      source: null,
      maxOutputTokens: null,
    });
  });
});
