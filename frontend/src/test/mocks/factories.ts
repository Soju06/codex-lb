import type { z } from "zod";
import type {
	AccountSummary,
	AccountTrendsResponse,
	OauthStartResponse,
	OauthStatusResponse,
} from "@/features/accounts/schemas";
import {
	AccountSummarySchema,
	AccountTrendsResponseSchema,
	OauthCompleteResponseSchema,
	OauthStartResponseSchema,
	OauthStatusResponseSchema,
} from "@/features/accounts/schemas";
import type { ApiKey, ApiKeyCreateResponse } from "@/features/api-keys/schemas";
import {
	ApiKeyCreateResponseSchema,
	ApiKeySchema,
} from "@/features/api-keys/schemas";
import type {
	ApiKeyTrendsResponse,
	ApiKeyUsage7DayResponse,
} from "@/features/apis/schemas";
import {
	ApiKeyTrendsResponseSchema,
	ApiKeyUsage7DayResponseSchema,
} from "@/features/apis/schemas";
import type { AuthSession } from "@/features/auth/schemas";
import { AuthSessionSchema } from "@/features/auth/schemas";
import type {
	DashboardOverview,
	DashboardProjections,
	RequestLog,
	RequestLogFilterOptions,
	RequestLogsResponse,
	OverviewTimeframe,
} from "@/features/dashboard/schemas";
import {
	DEFAULT_OVERVIEW_TIMEFRAME,
	DashboardOverviewSchema,
	DashboardProjectionsSchema,
	RequestLogFilterOptionsSchema,
	RequestLogSchema,
	RequestLogsResponseSchema,
} from "@/features/dashboard/schemas";
import type { DashboardSettings, UpstreamProxyAdmin } from "@/features/settings/schemas";
import { DashboardSettingsSchema, UpstreamProxyAdminSchema } from "@/features/settings/schemas";
import type {
	QuotaPlannerDecision,
	QuotaPlannerForecast,
	QuotaPlannerSettings,
} from "@/features/quota-planner/schemas";
import {
	QuotaPlannerDecisionSchema,
	QuotaPlannerForecastSchema,
	QuotaPlannerSettingsSchema,
	QuotaPlannerWarmupActionResponseSchema,
} from "@/features/quota-planner/schemas";

// Backward-compatible type aliases
export type RequestLogEntry = RequestLog;
export type DashboardAuthSession = AuthSession;
export type { QuotaPlannerDecision, QuotaPlannerForecast, QuotaPlannerSettings };
export type QuotaPlannerWarmupActionResponse = z.infer<typeof QuotaPlannerWarmupActionResponseSchema>;
export type OauthCompleteResponse = z.infer<typeof OauthCompleteResponseSchema>;

export type {
	AccountSummary,
	AccountTrendsResponse,
	DashboardOverview,
	DashboardProjections,
	RequestLogsResponse,
	RequestLogFilterOptions,
	DashboardSettings,
	UpstreamProxyAdmin,
	OauthStartResponse,
	OauthStatusResponse,
	ApiKey,
	ApiKeyCreateResponse,
	ApiKeyTrendsResponse,
	ApiKeyUsage7DayResponse,
};

const BASE_TIME = new Date("2026-01-01T12:00:00Z");

function offsetIso(minutes: number): string {
	return new Date(BASE_TIME.getTime() + minutes * 60_000).toISOString();
}

export function createAccountSummary(
	overrides: Partial<AccountSummary> = {},
): AccountSummary {
	return AccountSummarySchema.parse({
		accountId: "acc_primary",
		email: "primary@example.com",
		alias: null,
		displayName: "primary@example.com",
		planType: "plus",
		routingPolicy: "normal",
		status: "active",
		securityWorkAuthorized: false,
		usage: {
			primaryRemainingPercent: 82,
			secondaryRemainingPercent: 67,
			monthlyRemainingPercent: null,
		},
		resetAtPrimary: offsetIso(60),
		resetAtSecondary: offsetIso(24 * 60),
		resetAtMonthly: null,
		windowMinutesPrimary: 300,
		windowMinutesSecondary: 10_080,
		windowMinutesMonthly: null,
		capacityCreditsPrimary: 225,
		remainingCreditsPrimary: 184.5,
		capacityCreditsSecondary: 7_560,
		remainingCreditsSecondary: 5_065.2,
		capacityCreditsMonthly: null,
		remainingCreditsMonthly: null,
		creditsHas: true,
		creditsUnlimited: false,
		creditsBalance: 932,
		auth: {
			access: { expiresAt: offsetIso(30), state: null },
			refresh: { state: "stored" },
			idToken: { state: "parsed" },
		},
		limitWarmupEnabled: false,
		limitWarmup: null,
		...overrides,
	});
}

export function createDefaultAccounts(): AccountSummary[] {
	return [
		createAccountSummary(),
		createAccountSummary({
			accountId: "acc_secondary",
			email: "secondary@example.com",
			displayName: "secondary@example.com",
			status: "paused",
			usage: {
				primaryRemainingPercent: 45,
				secondaryRemainingPercent: 12,
			},
		}),
	];
}

function createTrendPoints(
	baseValue: number,
	count = 28,
	bucketSeconds = 6 * 3600,
): Array<{ t: string; v: number }> {
	return Array.from({ length: count }, (_, i) => ({
		t: new Date(BASE_TIME.getTime() - (count - i) * bucketSeconds * 1000).toISOString(),
		v: Math.max(0, baseValue + Math.sin(i) * baseValue * 0.3),
	}));
}

function createOverviewTimeframe(
	key: OverviewTimeframe = DEFAULT_OVERVIEW_TIMEFRAME,
) {
	switch (key) {
		case "1d":
			return {
				key,
				windowMinutes: 1_440,
				bucketSeconds: 3_600,
				bucketCount: 24,
			};
		case "30d":
			return {
				key,
				windowMinutes: 43_200,
				bucketSeconds: 86_400,
				bucketCount: 30,
			};
		case "7d":
		default:
			return {
				key: "7d" as const,
				windowMinutes: 10_080,
				bucketSeconds: 21_600,
				bucketCount: 28,
			};
	}
}

export function createDashboardOverview(
	overrides: Partial<DashboardOverview> = {},
): DashboardOverview {
	const timeframe = overrides.timeframe ?? createOverviewTimeframe();
	const accounts = overrides.accounts ?? createDefaultAccounts();
	const response = {
		lastSyncAt: offsetIso(-5),
		timeframe,
		accounts,
		summary: {
			primaryWindow: {
				remainingPercent: 63.5,
				capacityCredits: 225,
				remainingCredits: 142.875,
				resetAt: offsetIso(60),
				windowMinutes: 300,
			},
			secondaryWindow: {
				remainingPercent: 55.2,
				capacityCredits: 7560,
				remainingCredits: 4173.12,
				resetAt: offsetIso(24 * 60),
				windowMinutes: 10_080,
			},
			cost: {
				currency: "USD",
				totalUsd: 1.82,
			},
			metrics: {
				requests: 228,
				tokens: 45_000,
				cachedInputTokens: 8_200,
				errorRate: 0.028,
				errorCount: 6,
				topError: "rate_limit_exceeded",
			},
		},
		windows: {
			primary: {
				windowKey: "primary",
				windowMinutes: 300,
				accounts: accounts.map((account) => ({
					accountId: account.accountId,
					remainingPercentAvg: account.usage?.primaryRemainingPercent ?? 0,
					capacityCredits: 225,
					remainingCredits:
						((account.usage?.primaryRemainingPercent ?? 0) / 100) * 225,
				})),
			},
			secondary: {
				windowKey: "secondary",
				windowMinutes: 10_080,
				accounts: accounts.map((account) => ({
					accountId: account.accountId,
					remainingPercentAvg: account.usage?.secondaryRemainingPercent ?? 0,
					capacityCredits: account.capacityCreditsSecondary ?? 7560,
					remainingCredits:
						account.remainingCreditsSecondary ??
						((account.usage?.secondaryRemainingPercent ?? 0) / 100) *
							(account.capacityCreditsSecondary ?? 7560),
				})),
			},
		},
		trends: {
			requests: createTrendPoints(8, timeframe.bucketCount, timeframe.bucketSeconds),
			tokens: createTrendPoints(1600, timeframe.bucketCount, timeframe.bucketSeconds),
			cost: createTrendPoints(0.065, timeframe.bucketCount, timeframe.bucketSeconds),
			errorRate: createTrendPoints(0.03, timeframe.bucketCount, timeframe.bucketSeconds),
		},
		depletionPrimary: {
			risk: 0.55,
			riskLevel: "warning" as const,
			burnRate: 1.1,
			safeUsagePercent: 72.0,
			projectedExhaustionAt: null,
			secondsUntilExhaustion: null,
		},
		depletionSecondary: {
			risk: 0.65,
			riskLevel: "warning" as const,
			burnRate: 1.4,
			safeUsagePercent: 58.0,
			projectedExhaustionAt: null,
			secondsUntilExhaustion: null,
		},
		...overrides,
	};
	return DashboardOverviewSchema.parse(response);
}

export function createDashboardProjections(
	overrides: Partial<DashboardProjections> = {},
): DashboardProjections {
	return DashboardProjectionsSchema.parse({
		depletionPrimary: {
			risk: 0.55,
			riskLevel: "warning" as const,
			burnRate: 1.1,
			safeUsagePercent: 72.0,
			projectedExhaustionAt: null,
			secondsUntilExhaustion: null,
		},
		depletionSecondary: {
			risk: 0.65,
			riskLevel: "warning" as const,
			burnRate: 1.4,
			safeUsagePercent: 58.0,
			projectedExhaustionAt: null,
			secondsUntilExhaustion: null,
		},
		weeklyCreditPace: null,
		...overrides,
	});
}

export function createRequestLogEntry(
	overrides: Partial<RequestLogEntry> = {},
): RequestLogEntry {
	return RequestLogSchema.parse({
		requestedAt: offsetIso(-1),
		accountId: "acc_primary",
		apiKeyId: "key_1",
		apiKeyName: "Primary Key",
		requestId: "req_1",
		requestKind: "normal",
		model: "gpt-5.1",
		source: null,
		transport: "http",
		useragent: null,
		useragentGroup: null,
		serviceTier: null,
		requestedServiceTier: null,
		actualServiceTier: null,
		status: "ok",
		errorCode: null,
		errorMessage: null,
		tokens: 1800,
		inputTokens: 1200,
		outputTokens: 600,
		cachedInputTokens: 320,
		reasoningEffort: null,
		costUsd: 0.0132,
		costBreakdown: {
			inputUsd: 0.0054,
			cachedInputUsd: 0.0012,
			outputUsd: 0.0066,
			totalUsd: 0.0132,
		},
		latencyMs: 920,
		...overrides,
	});
}

export function createDefaultRequestLogs(): RequestLogEntry[] {
	return [
		createRequestLogEntry(),
		createRequestLogEntry({
			requestId: "req_2",
			accountId: "acc_secondary",
			apiKeyId: "key_2",
			apiKeyName: "Secondary Key",
			status: "rate_limit",
			errorCode: "rate_limit_exceeded",
			errorMessage: "Rate limit reached",
			tokens: 0,
			cachedInputTokens: null,
			costUsd: 0,
			requestedAt: offsetIso(-2),
		}),
		createRequestLogEntry({
			requestId: "req_3",
			apiKeyId: null,
			apiKeyName: null,
			status: "quota",
			errorCode: "insufficient_quota",
			errorMessage: "Quota exceeded",
			tokens: 0,
			cachedInputTokens: null,
			costUsd: 0,
			requestedAt: offsetIso(-3),
		}),
	];
}

export function createRequestLogsResponse(
	requests: RequestLogEntry[],
	total: number,
	hasMore: boolean,
): RequestLogsResponse {
	return RequestLogsResponseSchema.parse({
		requests,
		total,
		hasMore,
	});
}

export function createRequestLogFilterOptions(
	overrides: Partial<RequestLogFilterOptions> = {},
): RequestLogFilterOptions {
	return RequestLogFilterOptionsSchema.parse({
		accountIds: ["acc_primary", "acc_secondary"],
		modelOptions: [
			{ model: "gpt-5.1", reasoningEffort: null },
			{ model: "gpt-5.1", reasoningEffort: "high" },
		],
		apiKeys: [
			{ id: "key_1", name: "Default key", keyPrefix: "sk-test" },
			{ id: "key_2", name: "Read only key", keyPrefix: "sk-second" },
		],
		statuses: ["ok", "rate_limit", "quota"],
		...overrides,
	});
}

export function createDashboardAuthSession(
	overrides: Partial<DashboardAuthSession> = {},
): DashboardAuthSession {
	return AuthSessionSchema.parse({
		authenticated: true,
		passwordRequired: true,
		totpRequiredOnLogin: false,
		totpConfigured: true,
		authMode: "standard",
		passwordManagementEnabled: true,
		...overrides,
	});
}

export function createDashboardSettings(
	overrides: Partial<DashboardSettings> = {},
): DashboardSettings {
	return DashboardSettingsSchema.parse({
		stickyThreadsEnabled: true,
		upstreamStreamTransport: "default",
		upstreamProxyRoutingEnabled: false,
		upstreamProxyDefaultPoolId: null,
		preferEarlierResetAccounts: false,
		preferEarlierResetWindow: "secondary",
		routingStrategy: "usage_weighted",
		relativeAvailabilityPower: 2,
		relativeAvailabilityTopK: 5,
		singleAccountId: null,
		weeklyPaceWorkingDays: "0,1,2,3,4,5,6",
		openaiCacheAffinityMaxAgeSeconds: 300,
		dashboardSessionTtlSeconds: 43200,
		stickyReallocationBudgetThresholdPct: 95,
		stickyReallocationPrimaryBudgetThresholdPct: 95,
		stickyReallocationSecondaryBudgetThresholdPct: 100,
		warmupModel: "gpt-5.4-mini",
		importWithoutOverwrite: false,
		totpRequiredOnLogin: false,
		totpConfigured: true,
		apiKeyAuthEnabled: true,
		limitWarmupEnabled: false,
		limitWarmupWindows: "both",
		limitWarmupModel: "auto",
		limitWarmupPrompt: "Say OK.",
		limitWarmupCooldownSeconds: 3600,
		limitWarmupMinAvailablePercent: 100,
		...overrides,
	});
}

export function createQuotaPlannerSettings(
	overrides: Partial<QuotaPlannerSettings> = {},
): QuotaPlannerSettings {
	return QuotaPlannerSettingsSchema.parse({
		mode: "shadow",
		timezone: "UTC",
		workingDays: [0, 1, 2, 3, 4],
		workingHoursStart: "09:00",
		workingHoursEnd: "18:00",
		prewarmEnabled: true,
		prewarmLeadMinutes: 300,
		maxWarmupsPerDay: 3,
		maxWarmupCreditsPerDay: 0,
		minExpectedGain: 1,
		forecastQuantile: "p75",
		allowSyntheticTraffic: false,
		warmupModelPreference: null,
		dryRun: true,
		...overrides,
	});
}

export function createQuotaPlannerDecision(
	overrides: Partial<QuotaPlannerDecision> = {},
): QuotaPlannerDecision {
	return QuotaPlannerDecisionSchema.parse({
		id: "decision_1",
		createdAt: new Date().toISOString(),
		mode: "shadow",
		accountId: "acc_primary",
		action: "reserve",
		scheduledAt: new Date().toISOString(),
		executedAt: null,
		score: 12.5,
		reason: "forecast_phase_alignment",
		status: "skipped",
		idempotencyKey: "mock-decision-1",
		...overrides,
	});
}

export function createQuotaPlannerForecast(
	overrides: Partial<QuotaPlannerForecast> = {},
): QuotaPlannerForecast {
	return QuotaPlannerForecastSchema.parse({
		generatedAt: new Date().toISOString(),
		horizonHours: 36,
		slotSeconds: 900,
		totalDemandUnits: 48,
		peakSlotStart: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
		peakDemandUnits: 8,
		simulation: {
			loss: 4,
			unmetDemand: 3,
			wastedCapacity: 1,
			coldStartPenalty: 0,
			synchronizationPenalty: 0,
			forecastUnits: 48,
			servedUnits: 45,
		},
		slots: [],
		...overrides,
	});
}

export function createQuotaPlannerWarmupActionResponse(
	overrides: Partial<QuotaPlannerWarmupActionResponse> = {},
): QuotaPlannerWarmupActionResponse {
	return QuotaPlannerWarmupActionResponseSchema.parse({
		decisionId: "decision_1",
		status: "skipped",
		reason: "synthetic_traffic_disabled",
		requestId: null,
		executedAt: null,
		...overrides,
	});
}

export function createUpstreamProxyAdmin(
	overrides: Partial<UpstreamProxyAdmin> = {},
): UpstreamProxyAdmin {
	return UpstreamProxyAdminSchema.parse({
		routingEnabled: false,
		defaultPoolId: null,
		endpoints: [
			{
				id: "ep_primary",
				name: "Primary proxy",
				scheme: "http",
				host: "proxy-primary.test",
				port: 8080,
				username: "operator",
				isActive: true,
			},
		],
		pools: [
			{
				id: "pool_primary",
				name: "Primary pool",
				isActive: true,
				endpointIds: ["ep_primary"],
			},
		],
		bindings: [],
		...overrides,
	});
}

export function createOauthStartResponse(
	overrides: Partial<OauthStartResponse> = {},
): OauthStartResponse {
	return OauthStartResponseSchema.parse({
		method: "browser",
		authorizationUrl: "https://auth.example.com/start",
		callbackUrl: "http://localhost:3000/api/oauth/callback",
		verificationUrl: null,
		userCode: null,
		deviceAuthId: null,
		intervalSeconds: null,
		expiresInSeconds: null,
		...overrides,
	});
}

export function createOauthStatusResponse(
	overrides: Partial<OauthStatusResponse> = {},
): OauthStatusResponse {
	return OauthStatusResponseSchema.parse({
		status: "pending",
		errorMessage: null,
		...overrides,
	});
}

export function createOauthCompleteResponse(
	overrides: Partial<OauthCompleteResponse> = {},
): OauthCompleteResponse {
	return OauthCompleteResponseSchema.parse({
		status: "ok",
		...overrides,
	});
}

export function createApiKey(overrides: Partial<ApiKey> = {}): ApiKey {
	return ApiKeySchema.parse({
		id: "key_1",
		name: "Default key",
		keyPrefix: "sk-test",
		allowedModels: ["gpt-5.1"],
		applyToCodexModel: false,
		expiresAt: null,
		isActive: true,
		accountAssignmentScopeEnabled: false,
		assignedAccountIds: [],
		createdAt: offsetIso(-60),
		lastUsedAt: offsetIso(-5),
		usageSummary: {
			requestCount: 150,
			totalTokens: 50_000,
			cachedInputTokens: 10_000,
			totalCostUsd: 1.23,
		},
		limits: [
			{
				id: 1,
				limitType: "total_tokens",
				limitWindow: "weekly",
				maxValue: 1_000_000,
				currentValue: 125_000,
				modelFilter: null,
				resetAt: offsetIso(7 * 24 * 60),
			},
		],
		...overrides,
	});
}

export function createApiKeyCreateResponse(
	overrides: Partial<ApiKeyCreateResponse> = {},
): ApiKeyCreateResponse {
	return ApiKeyCreateResponseSchema.parse({
		...createApiKey(),
		key: "sk-test-generated-secret",
		...overrides,
	});
}

export function createDefaultApiKeys(): ApiKey[] {
	return [
		createApiKey(),
		createApiKey({
			id: "key_2",
			name: "Read only key",
			keyPrefix: "sk-second",
			allowedModels: ["gpt-4o-mini"],
			isActive: false,
			expiresAt: null,
			lastUsedAt: null,
			usageSummary: {
				requestCount: 42,
				totalTokens: 12_500,
				cachedInputTokens: 2_200,
				totalCostUsd: 0.42,
			},
			limits: [],
		}),
	];
}

function createUsageTrendPoints(
	basePercent: number,
	count = 28,
): Array<{ t: string; v: number }> {
	return Array.from({ length: count }, (_, i) => ({
		t: new Date(BASE_TIME.getTime() - (count - i) * 6 * 3600_000).toISOString(),
		v: Math.max(0, Math.min(100, basePercent + Math.sin(i) * 15)),
	}));
}

export function createAccountTrends(
	accountId: string,
	overrides: Partial<AccountTrendsResponse> = {},
): AccountTrendsResponse {
	return AccountTrendsResponseSchema.parse({
		accountId,
		primary: createUsageTrendPoints(80),
		secondary: createUsageTrendPoints(55),
		...overrides,
	});
}

function createApiKeyTrendPoints(count = 28): Array<{ t: string; v: number }> {
	return Array.from({ length: count }, (_, i) => ({
		t: new Date(BASE_TIME.getTime() - (count - i) * 6 * 3600_000).toISOString(),
		v: 10_000 + Math.round(Math.sin(i) * 5_000),
	}));
}

export function createApiKeyTrends(
	overrides: Partial<ApiKeyTrendsResponse> = {},
): ApiKeyTrendsResponse {
	return ApiKeyTrendsResponseSchema.parse({
		keyId: "key_1",
		cost: createApiKeyTrendPoints().map((p) => ({
			...p,
			v: +(p.v * 0.001).toFixed(4),
		})),
		tokens: createApiKeyTrendPoints(),
		...overrides,
	});
}

export function createApiKeyUsage7Day(
	overrides: Partial<ApiKeyUsage7DayResponse> = {},
): ApiKeyUsage7DayResponse {
	return ApiKeyUsage7DayResponseSchema.parse({
		keyId: "key_1",
		totalTokens: 280_000,
		cachedInputTokens: 45_000,
		totalRequests: 350,
		totalCostUsd: 2.47,
		...overrides,
	});
}
