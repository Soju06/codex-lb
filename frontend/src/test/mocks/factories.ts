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
import type { ModelSource } from "@/features/model-sources/schemas";
import { ModelSourceSchema } from "@/features/model-sources/schemas";
import type { AccessSummary, AuthSession, AuthSessionUser } from "@/features/auth/schemas";
import { AuthSessionSchema } from "@/features/auth/schemas";
import type {
	DashboardRole,
	DashboardUser,
	PendingInvite,
	PermissionDescriptor,
} from "@/features/access/api";
import type { AuditEntry, AuthProvider, RoleMapping } from "@/features/organisation/api";
import type {
	DashboardOverview,
	DashboardProjections,
	RequestLog,
	RequestLogFilterOptions,
	RequestLogsResponse,
	OverviewTimeframe,
} from "@/features/dashboard/schemas";
import {
	ConversationDetailsSchema,
	ConversationEntrySchema,
	ConversationsResponseSchema,
	ConversationModelStatSchema,
	DEFAULT_OVERVIEW_TIMEFRAME,
	DashboardOverviewSchema,
	DashboardProjectionsSchema,
	RequestLogFilterOptionsSchema,
	RequestLogSchema,
	RequestLogsResponseSchema,
} from "@/features/dashboard/schemas";
import type {
	DashboardSettings,
	ModelContextWindowOverrides,
	SubscriptionOverflowPreflight,
	TelemetryConsent,
	TelemetrySnapshotEnvelope,
	UpstreamProxyAdmin,
} from "@/features/settings/schemas";
import {
	DashboardSettingsSchema,
	ModelContextWindowOverridesSchema,
	SubscriptionOverflowPreflightSchema,
	TelemetryConsentSchema,
	TelemetrySnapshotEnvelopeSchema,
	UpstreamProxyAdminSchema,
} from "@/features/settings/schemas";
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
export type ConversationEntry = z.infer<typeof ConversationEntrySchema>;
export type ConversationsResponse = z.infer<typeof ConversationsResponseSchema>;
export type ConversationDetails = z.infer<typeof ConversationDetailsSchema>;
export type ConversationModelStat = z.infer<typeof ConversationModelStatSchema>;
export type { QuotaPlannerDecision, QuotaPlannerForecast, QuotaPlannerSettings };
export type QuotaPlannerWarmupActionResponse = z.infer<typeof QuotaPlannerWarmupActionResponseSchema>;
export type { ModelContextWindowOverrides };
export type OauthCompleteResponse = z.infer<typeof OauthCompleteResponseSchema>;

export type {
	AccountSummary,
	AccountTrendsResponse,
	DashboardOverview,
	DashboardProjections,
	RequestLogsResponse,
	RequestLogFilterOptions,
	DashboardSettings,
	TelemetryConsent,
	UpstreamProxyAdmin,
	OauthStartResponse,
	OauthStatusResponse,
	ApiKey,
	ApiKeyCreateResponse,
	ApiKeyTrendsResponse,
	ApiKeyUsage7DayResponse,
	ModelSource,
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
		chatgptAccountId: "chatgpt_acc_primary",
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

export function createModelSource(
	overrides: Partial<ModelSource> = {},
): ModelSource {
	return ModelSourceSchema.parse({
		id: "src_vllm",
		name: "vLLM",
		kind: "openai_compatible",
		baseUrl: "http://localhost:8000/v1",
		isEnabled: true,
		healthStatus: "unknown",
		supportsChatCompletions: true,
		supportsResponses: false,
		supportsAudioTranscriptions: false,
		supportsEmbeddings: false,
		timeoutSeconds: null,
		maxConcurrency: null,
		createdAt: offsetIso(-30),
		updatedAt: offsetIso(-5),
		models: [
			{
				id: 1,
				sourceId: "src_vllm",
				model: "local-coder",
				displayName: "local-coder",
				contextWindow: 8192,
				maxOutputTokens: 1024,
				supportsStreaming: true,
				supportsTools: true,
				supportsVision: false,
				inputPer1M: null,
				cachedInputPer1M: null,
				outputPer1M: null,
				audioPerMinute: null,
				rawMetadataJson: null,
				isEnabled: true,
				createdAt: offsetIso(-30),
				updatedAt: offsetIso(-5),
			},
		],
		...overrides,
	});
}

export function createDefaultModelSources(): ModelSource[] {
	return [createModelSource()];
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
				conversationRequests: 0,
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
			conversations: createTrendPoints(1, timeframe.bucketCount, timeframe.bucketSeconds),
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
		clientIp: null,
		conversationId: null,
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
		bootstrapRequired: false,
		bootstrapTokenConfigured: false,
		authMode: "standard",
		passwordManagementEnabled: true,
		passwordSessionActive: false,
		role: "admin",
		permissions: ADMIN_PERMISSIONS,
		guestAccessEnabled: false,
		guestPasswordRequired: false,
		breakGlassSession: false,
		...overrides,
	});
}

// Wire form of the preset grants (`permission_strings` in the backend): the
// coarse aliases first, then every `<permission>:<scope>` entry.
export const ADMIN_PERMISSIONS: string[] = [
	"read",
	"write",
	"accounts:export:all",
	"accounts:read:all",
	"accounts:write:all",
	"api_keys:assign:all",
	"api_keys:read:all",
	"api_keys:write:all",
	"audit:read:all",
	"conversations:read:all",
	"dashboard:read:all",
	"ops:write:all",
	"roles:manage:all",
	"security:write:all",
	"users:manage:all",
];

export const OPERATOR_PERMISSIONS: string[] = [
	"read",
	"write",
	"accounts:read:all",
	"accounts:write:all",
	"api_keys:assign:all",
	"api_keys:read:all",
	"api_keys:write:all",
	"dashboard:read:all",
	"ops:write:all",
];

export const VIEWER_PERMISSIONS: string[] = ["read", "accounts:read:all", "dashboard:read:all"];

export const GUEST_PERMISSIONS: string[] = VIEWER_PERMISSIONS;

export function createSessionUser(overrides: Partial<AuthSessionUser> = {}): AuthSessionUser {
	return {
		id: "user_admin",
		username: "admin",
		displayName: null,
		role: { id: "role_admin", slug: "admin", name: "Admin", kind: "preset" },
		...overrides,
	};
}

export function createAccessSummary(overrides: Partial<AccessSummary> = {}): AccessSummary {
	return {
		usersTotal: 1,
		usersActive: 1,
		usersInvited: 0,
		usersDisabled: 0,
		pendingInvites: 0,
		nonAdminUsers: 0,
		customRoles: 0,
		providersEnabled: ["password"],
		roleMappings: 0,
		scimTokens: 0,
		auditSinks: 0,
		localLoginPolicy: "enabled",
		...overrides,
	};
}

// ── Dashboard users / roles (people tab) ──

export const PRESET_ROLE_IDS = {
	admin: "role_admin",
	operator: "role_operator",
	member: "role_member",
	viewer: "role_viewer",
	guest: "role_guest",
} as const;

export const ASSIGNABLE_ROLE_IDS: string[] = [
	PRESET_ROLE_IDS.admin,
	PRESET_ROLE_IDS.operator,
	PRESET_ROLE_IDS.viewer,
];

const ALL_PERMISSIONS = [
	"accounts:export",
	"accounts:read",
	"accounts:write",
	"api_keys:assign",
	"api_keys:read",
	"api_keys:write",
	"audit:read",
	"conversations:read",
	"dashboard:read",
	"ops:write",
	"roles:manage",
	"security:write",
	"users:manage",
];

function grantsOf(permissions: string[], scope: "all" | "own" = "all") {
	return permissions.map((permission) => ({ permission, scope }));
}

export function createDashboardRole(overrides: Partial<DashboardRole> = {}): DashboardRole {
	return {
		id: PRESET_ROLE_IDS.viewer,
		slug: "viewer",
		name: "Viewer",
		description: "Read-only access to the dashboard.",
		kind: "preset",
		locked: true,
		assignableToUsers: true,
		grants: grantsOf(["accounts:read", "dashboard:read"]),
		usersCount: 0,
		...overrides,
	};
}

export function createDefaultDashboardRoles(): DashboardRole[] {
	return [
		createDashboardRole({
			id: PRESET_ROLE_IDS.admin,
			slug: "admin",
			name: "Admin",
			description: "Everything.",
			grants: grantsOf(ALL_PERMISSIONS),
			usersCount: 1,
		}),
		createDashboardRole({
			id: PRESET_ROLE_IDS.guest,
			slug: "guest",
			name: "Guest",
			description: "Anonymous read-only access.",
			assignableToUsers: false,
		}),
		createDashboardRole({
			id: PRESET_ROLE_IDS.member,
			slug: "member",
			name: "Member",
			description: "Own API keys and own usage.",
			assignableToUsers: false,
			grants: grantsOf(["api_keys:read", "api_keys:write", "dashboard:read"], "own"),
		}),
		createDashboardRole({
			id: PRESET_ROLE_IDS.operator,
			slug: "operator",
			name: "Operator",
			description: "Accounts, API keys and operations.",
			grants: grantsOf([
				"accounts:read",
				"accounts:write",
				"api_keys:assign",
				"api_keys:read",
				"api_keys:write",
				"dashboard:read",
				"ops:write",
			]),
			usersCount: 1,
		}),
		createDashboardRole(),
	];
}

export function createPermissionDescriptors(): PermissionDescriptor[] {
	const descriptions: Record<string, string> = {
		"accounts:export": "Export upstream account credentials.",
		"accounts:read": "See upstream accounts and their usage windows.",
		"accounts:write": "Add, edit, pause and remove upstream accounts and their routing.",
		"api_keys:assign": "Assign upstream accounts, model sources and owners to API keys.",
		"api_keys:read": "See API keys, their policies and their usage.",
		"api_keys:write": "Create, edit, rotate and delete API keys.",
		"audit:read": "Read the audit log.",
		"conversations:read": "Read conversation contents and archives.",
		"dashboard:read": "Read the dashboard overview, reports, request logs and the model catalog.",
		"ops:write": "Change operational settings such as model sources, automations and the quota planner.",
		"roles:manage": "Create, edit and delete custom roles.",
		"security:write": "Change security settings: guest access, firewall, upstream proxy credentials.",
		"users:manage": "Invite, edit, disable and remove dashboard accounts.",
	};
	return ALL_PERMISSIONS.map((permission) => ({
		permission,
		description: descriptions[permission] ?? permission,
		implies: [],
		ownSupported: ["dashboard:read", "api_keys:read", "api_keys:write"].includes(permission),
		privileged: false,
	}));
}

export function createDashboardUser(overrides: Partial<DashboardUser> = {}): DashboardUser {
	return {
		id: "user_admin",
		username: "admin",
		displayName: null,
		email: null,
		role: { id: PRESET_ROLE_IDS.admin, slug: "admin", name: "Admin", kind: "preset" },
		roleSource: "manual",
		status: "active",
		isBreakGlass: true,
		totpConfigured: true,
		hasPassword: true,
		createdAt: "2026-01-01T00:00:00Z",
		lastLoginAt: "2026-01-31T17:00:00Z",
		pendingInvite: null,
		...overrides,
	};
}

export function createDefaultDashboardUsers(): DashboardUser[] {
	return [
		createDashboardUser(),
		createDashboardUser({
			id: "user_ops",
			username: "ops",
			displayName: "Sarah Kim",
			role: { id: PRESET_ROLE_IDS.operator, slug: "operator", name: "Operator", kind: "preset" },
			isBreakGlass: false,
			totpConfigured: false,
			lastLoginAt: "2026-01-30T09:00:00Z",
		}),
		createDashboardUser({
			id: "user_invited",
			username: "lee",
			role: { id: PRESET_ROLE_IDS.viewer, slug: "viewer", name: "Viewer", kind: "preset" },
			status: "invited",
			isBreakGlass: false,
			totpConfigured: false,
			hasPassword: false,
			lastLoginAt: null,
			pendingInvite: { expiresAt: new Date(Date.now() + 20 * 3600_000).toISOString(), ssoOnly: false },
		}),
	];
}

// ── Organisation (sign-in providers, group-to-role rules, refusals) ──

export const TRUSTED_HEADER_PROVIDER_ID = "provider_trusted_header";
export const PASSWORD_PROVIDER_ID = "provider_password";

export function createAuthProvider(overrides: Partial<AuthProvider> = {}): AuthProvider {
	return {
		id: TRUSTED_HEADER_PROVIDER_ID,
		kind: "trusted_header",
		providerKey: "default",
		label: "Reverse proxy",
		enabled: true,
		active: true,
		unknownIdentityRoleId: PRESET_ROLE_IDS.admin,
		noMatchRoleId: PRESET_ROLE_IDS.viewer,
		linkByEmail: false,
		skipRoleSync: false,
		idpMfaEnforced: false,
		// The reverse-proxy header names are topology; the backend returns them read-only.
		config: { identityHeader: "Remote-User", groupsHeader: "Remote-Groups" },
		createdAt: "2026-01-01T00:00:00Z",
		updatedAt: "2026-01-01T00:00:00Z",
		...overrides,
	};
}

export function createDefaultAuthProviders(): AuthProvider[] {
	return [
		createAuthProvider({
			id: PASSWORD_PROVIDER_ID,
			kind: "password",
			label: "Password",
			unknownIdentityRoleId: null,
			noMatchRoleId: null,
			config: {},
		}),
		createAuthProvider(),
	];
}

export function createRoleMapping(overrides: Partial<RoleMapping> = {}): RoleMapping {
	return {
		id: "mapping_engineering",
		provider: "trusted_header",
		providerKey: "default",
		claimName: "groups",
		claimValue: "engineering",
		roleId: PRESET_ROLE_IDS.operator,
		priority: 1,
		createdAt: "2026-01-01T00:00:00Z",
		updatedAt: "2026-01-01T00:00:00Z",
		...overrides,
	};
}

/** Two rules, winner first, exactly as the backend orders them. */
export function createDefaultRoleMappings(): RoleMapping[] {
	return [
		createRoleMapping({ id: "mapping_platform", claimValue: "platform", priority: 2 }),
		createRoleMapping(),
	];
}

export function createRefusedSignIn(overrides: Partial<AuditEntry> = {}): AuditEntry {
	return {
		id: 1,
		timestamp: "2026-01-31T09:00:00Z",
		action: "login_failed",
		actorIp: "203.0.113.8",
		details: {
			method: "trusted_header",
			reason: "unknown_identity",
			subject: "sarah@example.com",
			email: "sarah@example.com",
		},
		severity: "warning",
		...overrides,
	};
}

export function createDefaultRefusedSignIns(): AuditEntry[] {
	return [
		createRefusedSignIn(),
		createRefusedSignIn({
			id: 2,
			timestamp: "2026-01-30T11:30:00Z",
			details: {
				method: "trusted_header",
				reason: "unknown_identity",
				subject: "lee@example.com",
				email: "lee@example.com",
			},
		}),
	];
}

export function createPendingInvite(overrides: Partial<PendingInvite> = {}): PendingInvite {
	return {
		userId: "user_invited",
		username: "lee",
		roleId: PRESET_ROLE_IDS.viewer,
		expiresAt: new Date(Date.now() + 20 * 3600_000).toISOString(),
		createdByUserId: "user_admin",
		ssoOnly: false,
		...overrides,
	};
}

export function createDashboardSettings(
	overrides: Partial<DashboardSettings> = {},
): DashboardSettings {
	return DashboardSettingsSchema.parse({
		stickyThreadsEnabled: true,
		upstreamStreamTransport: "auto",
		httpDownstreamTransportPolicy: "smart",
		upstreamProxyRoutingEnabled: false,
		upstreamProxyDefaultPoolId: null,
		preferEarlierResetAccounts: false,
		preferEarlierResetWindow: "secondary",
		showResetCreditBadges: true,
		autoRedeemResetCreditsBeforeExpiry: false,
		showResetCreditExpiryBadge: true,
		routingStrategy: "usage_weighted",
		relativeAvailabilityPower: 2,
		relativeAvailabilityTopK: 5,
		singleAccountId: null,
		subscriptionOverflowSourceId: null,
		subscriptionOverflowDrainUntil: null,
		subscriptionOverflowPinsExpireBy: null,
		proxyAccountResponseCreateLimit: 4,
		proxyAccountResponseCreateLimitEnvironmentValue: 4,
		proxyAccountResponseCreateLimitOverride: 4,
		proxyAccountStreamLimit: 8,
		proxyAccountStreamLimitEnvironmentValue: 8,
		proxyAccountStreamLimitOverride: 8,
		proxyAccountStreamRecoveryReserve: 1,
		proxyAccountStreamRecoveryReserveEnvironmentValue: 1,
		proxyAccountStreamRecoveryReserveOverride: 1,
		proxyApiKeyFairShareCongestionThresholdPct: 0,
		proxyApiKeyFairShareCongestionThresholdPctEnvironmentValue: 0,
		proxyApiKeyFairShareCongestionThresholdPctOverride: 0,
		proxyOverloadIsolationSeconds: 1800,
		proxyAccountErrorRateWeightingEnabled: true,
		proxyAccountInflightPenaltyPct: 2.5,
		proxyAccountLeaseTokenWeight: 1,
		proxyAccountLeaseTtlSeconds: 900,
		weeklyPaceWorkingDays: "0,1,2,3,4,5,6",
		weeklyPaceSmoothingMinutes: 30,
		openaiCacheAffinityMaxAgeSeconds: 300,
		dashboardSessionTtlSeconds: 31536000,
		stickyReallocationBudgetThresholdPct: 95,
		stickyReallocationPrimaryBudgetThresholdPct: 95,
		stickyReallocationSecondaryBudgetThresholdPct: 100,
		warmupModel: "gpt-5.4-mini",
		importWithoutOverwrite: false,
		totpRequiredOnLogin: false,
		totpRequiredForAdminRole: false,
		localLoginPolicy: "enabled",
		usersWithoutTotpCount: 0,
		adminsWithoutTotpCount: 0,
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
		guestAccessEnabled: false,
		guestPasswordConfigured: false,
		limitWarmupStaggeredIdleEnabled: false,
		softDrainEnabled: true,
		deterministicFailoverEnabled: true,
		circuitBreakerEnabled: false,
		httpResponsesSessionBridgeCodexPrewarmEnabled: false,
		authGuardianEnabled: true,
		authGuardianBlockedByTopology: false,
		automationsSchedulerEnabled: true,
		rateLimitResetCreditsRefreshEnabled: true,
		// M5 conversation archive
		conversationArchiveEnabled: false,
		conversationArchiveDir: "/var/lib/codex-lb/conversation-archive",
		// R2 spool retention: the 7-day default and the floor at shipped defaults.
		httpResponsesSessionBridgeOperationSpoolRetentionSeconds: 604800,
		httpResponsesSessionBridgeOperationSpoolRetentionFloorSeconds: 7200,
		...overrides,
	});
}

export function createTelemetrySnapshotEnvelope(): TelemetrySnapshotEnvelope {
	return TelemetrySnapshotEnvelopeSchema.parse({
		instance_id: "00000000-0000-4000-8000-000000000000",
		timestamp: "2026-08-06T00:00:00Z",
		metrics: {
			schema_version: 1,
			consent: "undecided",
			instance_id: "00000000-0000-4000-8000-000000000000",
			version: "1.23.0",
			python: "3.13",
			os: "linux",
			arch: "x86_64",
			uptime_hours: 168,
			deploy: {
				method: "docker",
				db_backend: "sqlite",
				db_size_bucket: "<100MB",
				replicas: 1,
				reverse_proxy: true,
			},
			accounts: {
				pool_bucket: "2-5",
				plan_mix: { plus: "2-5", pro: "0", team: "0", free: "0" },
				workspace_accounts: false,
				routing_policy: "usage_weighted",
				limit_warmup_enabled: false,
				egress_proxy_used: false,
			},
			usage_7d: {
				requests: 1024,
				success_rate: 0.99,
				tokens_input: 1000000,
				tokens_output: 50000,
				tokens_cached_ratio: 0.8,
				cost_usd_bucket: "<10",
				request_kinds: { responses: 0.97, chat: 0.02, images: 0.01, unknown: 0.0 },
				transport_mix: { ws: 0.6, http_bridge: 0.4 },
				service_tier_mix: { default: 1.0, flex: 0.0, priority: 0.0 },
				clients: { "codex-cli": 0.9, other: 0.1 },
				clients_other_ratio: 0.1,
				models: [
					{
						name: "gpt-5.4-codex",
						share: 1.0,
						reasoning: { high: 0.5, medium: 0.5 },
						avg_output_tokens_bucket: "250-1k",
					},
				],
				latency_ms_p50: 1200,
				ttft_ms_p50: 800,
				ttft_ms_p95: 3400,
				rate_limit_429_ratio: 0.004,
				top_upstream_errors: ["server_overloaded"],
			},
			features: {
				api_firewall: false,
				quota_planner: false,
				sticky_sessions: true,
				conversation_archive: false,
				automations: false,
				fleet: false,
				model_sources_count: 0,
				api_keys_bucket: "2-5",
				prometheus: false,
				otel: false,
				dashboard_auth: true,
				reset_credits: true,
				image_api_used: false,
			},
		},
	});
}

export function createTelemetryConsent(
	overrides: Partial<TelemetryConsent> = {},
): TelemetryConsent {
	const base = {
		state: "enabled",
		source: "persisted",
		active: true,
		...overrides,
	};
	// Mirror the backend: the base GET attaches a preview envelope only for
	// the undecided/default (consent dialog) case; explicit overrides win.
	const preview =
		"preview" in overrides
			? overrides.preview
			: base.state === "undecided" && base.source === "default"
				? createTelemetrySnapshotEnvelope()
				: null;
	return TelemetryConsentSchema.parse({ ...base, preview });
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

export function createSubscriptionOverflowPreflight(
	overrides: Partial<SubscriptionOverflowPreflight> = {},
): SubscriptionOverflowPreflight {
	return SubscriptionOverflowPreflightSchema.parse({
		sourceId: "src_vllm",
		sourceName: "vLLM",
		sourceEnabled: true,
		eligible: true,
		blockers: [],
		drainUntil: null,
		servedModels: [
			{
				slug: "gpt-5.4",
				enabled: true,
				neverOverflows: false,
				neverOverflowsReason: null,
				undeclaredToolTypes: ["shell", "tool_search"],
				supportsVision: false,
				supportsStreaming: true,
				priced: false,
				contextWindowMismatch: { registry: 272000, source: 8192, maxOutputTokens: 1024 },
				warnings: ["undeclared_tool_types", "no_vision", "unpriced", "context_window_smaller"],
			},
			{
				slug: "gpt-5.6-sol",
				enabled: true,
				neverOverflows: true,
				neverOverflowsReason: "responses_lite",
				undeclaredToolTypes: [],
				supportsVision: true,
				supportsStreaming: true,
				priced: true,
				contextWindowMismatch: null,
				warnings: ["responses_lite_excluded"],
			},
		],
		missingModels: ["gpt-5.5"],
		scopedApiKeyCount: 1,
		livePinCount: 2,
		tombstoneCount: 1,
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

export function createModelContextWindowOverrides(
	overrides: Partial<ModelContextWindowOverrides> = {},
): ModelContextWindowOverrides {
	return ModelContextWindowOverridesSchema.parse({
		overrides: [
			{ slug: "gpt-5.4", contextWindow: 515000, source: "dashboard", envValue: 300000 },
			{ slug: "gpt-5.5", contextWindow: 400000, source: "env", envValue: 400000 },
		],
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
		transportPolicyOverride: null,
		expiresAt: null,
		isActive: true,
		accountAssignmentScopeEnabled: false,
		sourceAssignmentScopeEnabled: false,
		assignedAccountIds: [],
		assignedSourceIds: [],
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

export function createConversationEntry(
	overrides: Partial<ConversationEntry> = {},
): ConversationEntry {
	return ConversationEntrySchema.parse({
		conversationId: "conv_abc",
		firstRequest: offsetIso(-2),
		lastRequest: offsetIso(-1),
		requestCount: 1,
		representativeAccount: "acc_primary",
		remainingAccountCount: 1,
		apiKeyId: "key_1",
		apiKeyName: "Primary Key",
		representativeModel: "gpt-5.1",
		remainingModelCount: 1,
		totalTokens: 1800,
		cachedInputTokens: 320,
		totalCostUsd: 0.0132,
		...overrides,
	});
}

export function createDefaultConversations(): ConversationEntry[] {
	return [
		createConversationEntry(),
		createConversationEntry({
			conversationId: "conv_def",
			lastRequest: offsetIso(-2),
			representativeAccount: "acc_secondary",
			remainingAccountCount: 0,
			apiKeyId: "key_2",
			apiKeyName: "Secondary Key",
			representativeModel: "gpt-5.1-codex",
			remainingModelCount: 0,
			totalTokens: 4200,
			cachedInputTokens: 0,
			totalCostUsd: 0.04,
		}),
	];
}

export function createConversationsResponse(
	conversations: ConversationEntry[],
	total: number,
	hasMore: boolean,
): ConversationsResponse {
	return ConversationsResponseSchema.parse({
		conversations,
		total,
		hasMore,
	});
}

export function createConversationModelStat(
	overrides: Partial<ConversationModelStat> = {},
): ConversationModelStat {
	return ConversationModelStatSchema.parse({
		modelEffort: { model: "gpt-5.1", reasoningEffort: "high" },
		reqs: 4,
		totalElapsedTime: 1200,
		totalInputTokens: 1000,
		cachedInputTokens: 200,
		totalOutputTokens: 300,
		totalCostUsd: 0.05,
		...overrides,
	});
}

export function createConversationDetails(
	overrides: Partial<ConversationDetails> = {},
): ConversationDetails {
	return ConversationDetailsSchema.parse({
		conversationId: "conv_abc",
		start: offsetIso(-10),
		latest: offsetIso(-1),
		accountCount: 2,
		totalElapsedTime: 4200,
		dominantUseragentGroup: "opencode",
		modelStats: [
			createConversationModelStat(),
			createConversationModelStat({
				modelEffort: { model: "gpt-5.1", reasoningEffort: null },
				reqs: 2,
				totalElapsedTime: 600,
				totalInputTokens: 500,
				cachedInputTokens: 0,
				totalOutputTokens: 100,
				totalCostUsd: 0.02,
			}),
		],
		...overrides,
	});
}
