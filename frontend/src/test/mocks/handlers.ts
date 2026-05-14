import { HttpResponse, http } from "msw";
import { z } from "zod";

import { LIMIT_TYPES, LIMIT_WINDOWS } from "@/features/api-keys/schemas";
import {
	type AccountSummary,
	type ApiKey,
	createAccountSummary,
	createAccountTrends,
	createApiKey,
	createApiKeyAccountUsage7Day,
	createApiKeyCreateResponse,
	createApiKeyTrends,
	createApiKeyUsage7Day,
	createDashboardAuthSession,
	createDashboardOverview,
	createDashboardSettings,
	createDefaultAccounts,
	createDefaultApiKeys,
	createDefaultRequestLogs,
	createOauthCompleteResponse,
	createOauthStartResponse,
	createOauthStatusResponse,
	createRequestLogFilterOptions,
	createRequestLogsResponse,
	type DashboardAuthSession,
	type DashboardSettings,
	type RequestLogEntry,
} from "@/test/mocks/factories";

const MODEL_OPTION_DELIMITER = ":::";
const STATUS_ORDER = ["ok", "rate_limit", "quota", "error"] as const;

// ── Zod schemas for mock request bodies ──

const OauthStartPayloadSchema = z
	.object({
		forceMethod: z.string().optional(),
	})
	.passthrough();

const ApiKeyCreatePayloadSchema = z
	.object({
		name: z.string().optional(),
	})
	.passthrough();

const FirewallIpCreatePayloadSchema = z
	.object({
		ipAddress: z.string().optional(),
	})
	.passthrough();

const ApiKeyUpdatePayloadSchema = z
	.object({
		name: z.string().optional(),
		allowedModels: z.array(z.string()).nullable().optional(),
		isActive: z.boolean().optional(),
		assignedAccountIds: z.array(z.string()).optional(),
		resetUsage: z.boolean().optional(),
		limits: z
			.array(
				z.object({
					limitType: z.enum(LIMIT_TYPES),
					limitWindow: z.enum(LIMIT_WINDOWS),
					maxValue: z.number(),
					modelFilter: z.string().nullable().optional(),
				}),
			)
			.optional(),
	})
	.passthrough();

const SettingsPayloadSchema = z
	.object({
		stickyThreadsEnabled: z.boolean().optional(),
		upstreamStreamTransport: z
			.enum(["default", "auto", "http", "websocket"])
			.optional(),
		preferEarlierResetAccounts: z.boolean().optional(),
		routingStrategy: z.enum(["usage_weighted", "round_robin", "capacity_weighted"]).optional(),
		openaiCacheAffinityMaxAgeSeconds: z.number().int().positive().optional(),
		importWithoutOverwrite: z.boolean().optional(),
		totpRequiredOnLogin: z.boolean().optional(),
		totpConfigured: z.boolean().optional(),
		apiKeyAuthEnabled: z.boolean().optional(),
	})
	.passthrough();

// ── Helpers ──

async function parseJsonBody<T>(
	request: Request,
	schema: z.ZodType<T>,
): Promise<T | null> {
	try {
		const raw: unknown = await request.json();
		const result = schema.safeParse(raw);
		return result.success ? result.data : null;
	} catch {
		return null;
	}
}

type MockState = {
	accounts: AccountSummary[];
	requestLogs: RequestLogEntry[];
	authSession: DashboardAuthSession;
	settings: DashboardSettings;
	apiKeys: ApiKey[];
	firewallEntries: Array<{ ipAddress: string; createdAt: string }>;
	stickySessions: Array<{
		key: string;
		displayName: string;
		kind: "codex_session" | "sticky_thread" | "prompt_cache";
		createdAt: string;
		updatedAt: string;
		expiresAt: string | null;
		isStale: boolean;
	}>;
};

function createInitialState(): MockState {
	return {
		accounts: createDefaultAccounts(),
		requestLogs: createDefaultRequestLogs(),
		authSession: createDashboardAuthSession(),
		settings: createDashboardSettings(),
		apiKeys: createDefaultApiKeys(),
		firewallEntries: [],
		stickySessions: [],
	};
}

let state: MockState = createInitialState();

export function resetMockState(): void {
	state = createInitialState();
}

function parseDateValue(value: string | null): number | null {
	if (!value) {
		return null;
	}
	const timestamp = new Date(value).getTime();
	return Number.isNaN(timestamp) ? null : timestamp;
}

function filterRequestLogs(
	url: URL,
	options?: { includeStatuses?: boolean; ignoreApiKeyIds?: boolean },
): RequestLogEntry[] {
	const includeStatuses = options?.includeStatuses ?? true;
	const ignoreApiKeyIds = options?.ignoreApiKeyIds ?? false;
	const accountIds = new Set(url.searchParams.getAll("accountId"));
	const apiKeyIds = new Set(url.searchParams.getAll("apiKeyId"));
	const statuses = new Set(
		url.searchParams.getAll("status").map((value) => value.toLowerCase()),
	);
	const models = new Set(url.searchParams.getAll("model"));
	const reasoningEfforts = new Set(url.searchParams.getAll("reasoningEffort"));
	const modelOptions = new Set(url.searchParams.getAll("modelOption"));
	const search = (url.searchParams.get("search") || "").trim().toLowerCase();
	const since = parseDateValue(url.searchParams.get("since"));
	const until = parseDateValue(url.searchParams.get("until"));

	return state.requestLogs.filter((entry) => {
		if (
			accountIds.size > 0 &&
			(!entry.accountId || !accountIds.has(entry.accountId))
		) {
			return false;
		}
		if (
			!ignoreApiKeyIds &&
			apiKeyIds.size > 0 &&
			(!entry.apiKeyId || !apiKeyIds.has(entry.apiKeyId))
		) {
			return false;
		}

		if (
			includeStatuses &&
			statuses.size > 0 &&
			!statuses.has("all") &&
			!statuses.has(entry.status)
		) {
			return false;
		}

		if (models.size > 0 && !models.has(entry.model)) {
			return false;
		}

		if (reasoningEfforts.size > 0) {
			const effort = entry.reasoningEffort ?? "";
			if (!reasoningEfforts.has(effort)) {
				return false;
			}
		}

		if (modelOptions.size > 0) {
			const key = `${entry.model}${MODEL_OPTION_DELIMITER}${entry.reasoningEffort ?? ""}`;
			const matchNoEffort = modelOptions.has(entry.model);
			if (!modelOptions.has(key) && !matchNoEffort) {
				return false;
			}
		}

		const timestamp = new Date(entry.requestedAt).getTime();
		if (since !== null && timestamp < since) {
			return false;
		}
		if (until !== null && timestamp > until) {
			return false;
		}

		if (search.length > 0) {
			const haystack = [
				entry.accountId,
				entry.apiKeyId,
				entry.apiKeyName,
				entry.requestId,
				entry.model,
				entry.reasoningEffort,
				entry.errorCode,
				entry.errorMessage,
				entry.status,
			]
				.filter(Boolean)
				.join(" ")
				.toLowerCase();
			if (!haystack.includes(search)) {
				return false;
			}
		}

		return true;
	});
}

function requestLogOptionsFromEntries(
	entries: RequestLogEntry[],
	apiKeyEntries: RequestLogEntry[] = entries,
) {
	const accountIds = [
		...new Set(
			entries
				.map((entry) => entry.accountId)
				.filter((id): id is string => id != null),
		),
	].sort();

	const modelMap = new Map<
		string,
		{ model: string; reasoningEffort: string | null }
	>();
	for (const entry of entries) {
		const key = `${entry.model}${MODEL_OPTION_DELIMITER}${entry.reasoningEffort ?? ""}`;
		if (!modelMap.has(key)) {
			modelMap.set(key, {
				model: entry.model,
				reasoningEffort: entry.reasoningEffort ?? null,
			});
		}
	}
	const modelOptionsList = [...modelMap.values()].sort((a, b) => {
		if (a.model !== b.model) {
			return a.model.localeCompare(b.model);
		}
		return (a.reasoningEffort ?? "").localeCompare(b.reasoningEffort ?? "");
	});

	const apiKeyMap = new Map<
		string,
		{ id: string; name: string; keyPrefix: string | null }
	>();
	for (const entry of apiKeyEntries) {
		if (!entry.apiKeyId) continue;
		const apiKey = findApiKey(entry.apiKeyId);
		apiKeyMap.set(entry.apiKeyId, {
			id: entry.apiKeyId,
			name: apiKey?.name ?? entry.apiKeyName ?? entry.apiKeyId,
			keyPrefix: apiKey?.keyPrefix ?? null,
		});
	}
	const apiKeys = [...apiKeyMap.values()].sort((a, b) => {
		if (a.name !== b.name) {
			return a.name.localeCompare(b.name);
		}
		return (a.keyPrefix ?? "").localeCompare(b.keyPrefix ?? "");
	});

	const presentStatuses = new Set(entries.map((entry) => entry.status));
	const statuses = STATUS_ORDER.filter((status) => presentStatuses.has(status));

	return createRequestLogFilterOptions({
		accountIds,
		modelOptions: modelOptionsList,
		apiKeys,
		statuses: [...statuses],
	});
}

function findAccount(accountId: string): AccountSummary | undefined {
	return state.accounts.find((account) => account.accountId === accountId);
}

function findApiKey(keyId: string): ApiKey | undefined {
	return state.apiKeys.find((item) => item.id === keyId);
}

export const handlers = [
	http.get("/health", () => {
		return HttpResponse.json({ status: "ok" });
	}),

	http.get("/api/dashboard/overview", () => {
		return HttpResponse.json(
			createDashboardOverview({
				accounts: state.accounts,
			}),
		);
	}),

	http.get("/api/request-logs", ({ request }) => {
		const url = new URL(request.url);
		const filtered = filterRequestLogs(url);
		const total = filtered.length;
		const limitRaw = Number(url.searchParams.get("limit") ?? 50);
		const offsetRaw = Number(url.searchParams.get("offset") ?? 0);
		const limit =
			Number.isFinite(limitRaw) && limitRaw > 0 ? Math.floor(limitRaw) : 50;
		const offset =
			Number.isFinite(offsetRaw) && offsetRaw > 0 ? Math.floor(offsetRaw) : 0;
		const requests = filtered.slice(offset, offset + limit);
		return HttpResponse.json(
			createRequestLogsResponse(requests, total, offset + limit < total),
		);
	}),

	http.get("/api/request-logs/options", ({ request }) => {
		const url = new URL(request.url);
		const filtered = filterRequestLogs(url, {
			includeStatuses: false,
		});
		const apiKeyFiltered = filterRequestLogs(url, {
			includeStatuses: false,
			ignoreApiKeyIds: true,
		});
		return HttpResponse.json(requestLogOptionsFromEntries(filtered, apiKeyFiltered));
	}),

	http.get("/api/accounts", () => {
		return HttpResponse.json({ accounts: state.accounts });
	}),

	http.post("/api/accounts/import", async () => {
		const sequence = state.accounts.length + 1;
		const created = createAccountSummary({
			accountId: `acc_imported_${sequence}`,
			email: `imported-${sequence}@example.com`,
			displayName: `imported-${sequence}@example.com`,
			status: "active",
		});
		state.accounts = [...state.accounts, created];
		return HttpResponse.json({
			accountId: created.accountId,
			email: created.email,
			planType: created.planType,
			status: created.status,
		});
	}),

	http.post("/api/accounts/:accountId/pause", ({ params }) => {
		const accountId = String(params.accountId);
		const account = findAccount(accountId);
		if (!account) {
			return HttpResponse.json(
				{ error: { code: "account_not_found", message: "Account not found" } },
				{ status: 404 },
			);
		}
		account.status = "paused";
		return HttpResponse.json({ status: "paused" });
	}),

	http.post("/api/accounts/:accountId/reactivate", ({ params }) => {
		const accountId = String(params.accountId);
		const account = findAccount(accountId);
		if (!account) {
			return HttpResponse.json(
				{ error: { code: "account_not_found", message: "Account not found" } },
				{ status: 404 },
			);
		}
		account.status = "active";
		return HttpResponse.json({ status: "reactivated" });
	}),

	http.get("/api/accounts/:accountId/trends", ({ params }) => {
		const accountId = String(params.accountId);
		const account = findAccount(accountId);
		if (!account) {
			return HttpResponse.json(
				{ error: { code: "account_not_found", message: "Account not found" } },
				{ status: 404 },
			);
		}
		return HttpResponse.json(createAccountTrends(accountId));
	}),

	http.delete("/api/accounts/:accountId", ({ params }) => {
		const accountId = String(params.accountId);
		const exists = state.accounts.some(
			(account) => account.accountId === accountId,
		);
		if (!exists) {
			return HttpResponse.json(
				{ error: { code: "account_not_found", message: "Account not found" } },
				{ status: 404 },
			);
		}
		state.accounts = state.accounts.filter(
			(account) => account.accountId !== accountId,
		);
		return HttpResponse.json({ status: "deleted" });
	}),

	http.post("/api/oauth/start", async ({ request }) => {
		const payload = await parseJsonBody(request, OauthStartPayloadSchema);
		if (payload?.forceMethod === "device") {
			return HttpResponse.json(
				createOauthStartResponse({
					method: "device",
					authorizationUrl: null,
					callbackUrl: null,
					verificationUrl: "https://auth.example.com/device",
					userCode: "AAAA-BBBB",
					deviceAuthId: "device-auth-id",
					intervalSeconds: 5,
					expiresInSeconds: 900,
				}),
			);
		}
		return HttpResponse.json(createOauthStartResponse());
	}),

	http.get("/api/oauth/status", () => {
		return HttpResponse.json(createOauthStatusResponse());
	}),

	http.post("/api/oauth/complete", () => {
		return HttpResponse.json(createOauthCompleteResponse());
	}),

	http.get("/api/settings", () => {
		return HttpResponse.json(state.settings);
	}),

	http.get("/api/firewall/ips", () => {
		return HttpResponse.json({
			mode:
				state.firewallEntries.length === 0 ? "allow_all" : "allowlist_active",
			entries: state.firewallEntries,
		});
	}),

	http.post("/api/firewall/ips", async ({ request }) => {
		const payload = await parseJsonBody(request, FirewallIpCreatePayloadSchema);
		const ipAddress = String(payload?.ipAddress || "").trim();
		if (!ipAddress) {
			return HttpResponse.json(
				{ error: { code: "invalid_ip", message: "IP address is required" } },
				{ status: 400 },
			);
		}
		if (state.firewallEntries.some((entry) => entry.ipAddress === ipAddress)) {
			return HttpResponse.json(
				{ error: { code: "ip_exists", message: "IP address already exists" } },
				{ status: 409 },
			);
		}
		const created = { ipAddress, createdAt: new Date().toISOString() };
		state.firewallEntries = [...state.firewallEntries, created];
		return HttpResponse.json(created);
	}),

	http.delete("/api/firewall/ips/:ipAddress", ({ params }) => {
		const ipAddress = decodeURIComponent(String(params.ipAddress));
		const exists = state.firewallEntries.some(
			(entry) => entry.ipAddress === ipAddress,
		);
		if (!exists) {
			return HttpResponse.json(
				{ error: { code: "ip_not_found", message: "IP address not found" } },
				{ status: 404 },
			);
		}
		state.firewallEntries = state.firewallEntries.filter(
			(entry) => entry.ipAddress !== ipAddress,
		);
		return HttpResponse.json({ status: "deleted" });
	}),

	http.put("/api/settings", async ({ request }) => {
		const payload = await parseJsonBody(request, SettingsPayloadSchema);
		if (!payload) {
			return HttpResponse.json(state.settings);
		}
		state.settings = createDashboardSettings({
			...state.settings,
			...payload,
		});
		return HttpResponse.json(state.settings);
	}),

	http.get("/api/sticky-sessions", ({ request }) => {
		const url = new URL(request.url);
		const staleOnly = url.searchParams.get("staleOnly") === "true";
		const accountQuery = (url.searchParams.get("accountQuery") ?? "").trim().toLowerCase();
		const keyQuery = (url.searchParams.get("keyQuery") ?? "").trim().toLowerCase();
		const sortBy = url.searchParams.get("sortBy") ?? "updated_at";
		const sortDir = url.searchParams.get("sortDir") ?? "desc";
		const offset = Number(url.searchParams.get("offset") ?? "0");
		const limit = Number(url.searchParams.get("limit") ?? "10");
		const filteredEntries = state.stickySessions.filter((entry) => {
			if (staleOnly && !(entry.kind === "prompt_cache" && entry.isStale)) {
				return false;
			}
			if (accountQuery && !entry.displayName.toLowerCase().includes(accountQuery)) {
				return false;
			}
			if (keyQuery && !entry.key.toLowerCase().includes(keyQuery)) {
				return false;
			}
			return true;
		}).sort((left, right) => {
			const direction = sortDir === "asc" ? 1 : -1;
			if (sortBy === "account") {
				return left.displayName.localeCompare(right.displayName) * direction;
			}
			if (sortBy === "key") {
				return left.key.localeCompare(right.key) * direction;
			}
			const leftTime = Date.parse(sortBy === "created_at" ? left.createdAt : left.updatedAt);
			const rightTime = Date.parse(sortBy === "created_at" ? right.createdAt : right.updatedAt);
			if (leftTime !== rightTime) {
				return (leftTime - rightTime) * direction;
			}
			return left.key.localeCompare(right.key);
		});
		const entries = filteredEntries.slice(offset, offset + limit);
		const stalePromptCacheCount = state.stickySessions.filter(
			(entry) => entry.kind === "prompt_cache" && entry.isStale,
		).length;
		return HttpResponse.json({
			entries,
			stalePromptCacheCount,
			total: filteredEntries.length,
			hasMore: offset + entries.length < filteredEntries.length,
		});
	}),

	http.post("/api/sticky-sessions/delete", async ({ request }) => {
		const payload = (await parseJsonBody(
			request,
			z.object({
				sessions: z
					.array(
						z.object({
							key: z.string().min(1),
							kind: z.enum(["codex_session", "sticky_thread", "prompt_cache"]),
						}),
					)
					.min(1)
					.max(500)
					.refine(
						(sessions) =>
							new Set(sessions.map((session) => `${session.kind}:${session.key}`)).size === sessions.length,
						"Duplicate sticky session targets are not allowed",
					),
			}),
		)) ?? { sessions: [] };
		const targets = new Set(payload.sessions.map((session) => `${session.kind}:${session.key}`));
		const deleted = state.stickySessions
			.filter((entry) => targets.has(`${entry.kind}:${entry.key}`))
			.map((entry) => ({ key: entry.key, kind: entry.kind }));
		const deletedTargets = new Set(deleted.map((entry) => `${entry.kind}:${entry.key}`));
		state.stickySessions = state.stickySessions.filter(
			(entry) => !targets.has(`${entry.kind}:${entry.key}`),
		);
		return HttpResponse.json({
			deletedCount: deleted.length,
			deleted,
			failed: payload.sessions
				.filter((session) => !deletedTargets.has(`${session.kind}:${session.key}`))
				.map((session) => ({
					key: session.key,
					kind: session.kind,
					reason: "not_found",
				})),
		});
	}),

	http.post("/api/sticky-sessions/delete-filtered", async ({ request }) => {
		const payload = (await parseJsonBody(
			request,
			z.object({
				staleOnly: z.boolean().default(false),
				accountQuery: z.string().default(""),
				keyQuery: z.string().default(""),
			}),
		)) ?? {
			staleOnly: false,
			accountQuery: "",
			keyQuery: "",
		};
		const accountQuery = payload.accountQuery.trim().toLowerCase();
		const keyQuery = payload.keyQuery.trim().toLowerCase();
		const matched = state.stickySessions.filter((entry) => {
			if (payload.staleOnly && !(entry.kind === "prompt_cache" && entry.isStale)) {
				return false;
			}
			if (accountQuery && !entry.displayName.toLowerCase().includes(accountQuery)) {
				return false;
			}
			if (keyQuery && !entry.key.toLowerCase().includes(keyQuery)) {
				return false;
			}
			return true;
		});
		const targets = new Set(matched.map((entry) => `${entry.kind}:${entry.key}`));
		state.stickySessions = state.stickySessions.filter((entry) => !targets.has(`${entry.kind}:${entry.key}`));
		return HttpResponse.json({ deletedCount: matched.length });
	}),

	http.post("/api/sticky-sessions/purge", async ({ request }) => {
		const payload = (await parseJsonBody(
			request,
			z.object({ staleOnly: z.boolean().default(true) }),
		)) ?? {
			staleOnly: true,
		};
		if (payload.staleOnly) {
			const before = state.stickySessions.length;
			state.stickySessions = state.stickySessions.filter(
				(entry) => !entry.isStale,
			);
			return HttpResponse.json({
				deletedCount: before - state.stickySessions.length,
			});
		}
		const deletedCount = state.stickySessions.length;
		state.stickySessions = [];
		return HttpResponse.json({ deletedCount });
	}),

	http.get("/api/dashboard-auth/session", () => {
		return HttpResponse.json(state.authSession);
	}),

	http.post("/api/dashboard-auth/password/setup", () => {
		state.authSession = createDashboardAuthSession({
			authenticated: true,
			passwordRequired: true,
			totpRequiredOnLogin: false,
			totpConfigured: state.authSession.totpConfigured,
		});
		return HttpResponse.json(state.authSession);
	}),

	http.post("/api/dashboard-auth/password/login", () => {
		state.authSession = createDashboardAuthSession({
			...state.authSession,
			authenticated: !state.authSession.totpRequiredOnLogin,
		});
		return HttpResponse.json(state.authSession);
	}),

	http.post("/api/dashboard-auth/password/change", () => {
		return HttpResponse.json({ status: "ok" });
	}),

	http.delete("/api/dashboard-auth/password", () => {
		state.authSession = createDashboardAuthSession({
			authenticated: false,
			passwordRequired: false,
			totpRequiredOnLogin: false,
			totpConfigured: false,
		});
		return HttpResponse.json({ status: "ok" });
	}),

	http.post("/api/dashboard-auth/totp/setup/start", () => {
		return HttpResponse.json({
			secret: "JBSWY3DPEHPK3PXP",
			otpauthUri: "otpauth://totp/codex-lb?secret=JBSWY3DPEHPK3PXP",
			qrSvgDataUri: "data:image/svg+xml;base64,PHN2Zy8+",
		});
	}),

	http.post("/api/dashboard-auth/totp/setup/confirm", () => {
		state.authSession = createDashboardAuthSession({
			...state.authSession,
			totpConfigured: true,
			authenticated: true,
		});
		return HttpResponse.json({ status: "ok" });
	}),

	http.post("/api/dashboard-auth/totp/verify", () => {
		state.authSession = createDashboardAuthSession({
			...state.authSession,
			authenticated: true,
		});
		return HttpResponse.json(state.authSession);
	}),

	http.post("/api/dashboard-auth/totp/disable", () => {
		state.authSession = createDashboardAuthSession({
			...state.authSession,
			totpConfigured: false,
			totpRequiredOnLogin: false,
			authenticated: true,
		});
		return HttpResponse.json({ status: "ok" });
	}),

	http.post("/api/dashboard-auth/logout", () => {
		state.authSession = createDashboardAuthSession({
			...state.authSession,
			authenticated: false,
		});
		return HttpResponse.json({ status: "ok" });
	}),

	http.get("/api/models", () => {
		return HttpResponse.json({
			models: [
				{ id: "gpt-5.1", name: "GPT 5.1" },
				{ id: "gpt-5.1-codex-mini", name: "GPT 5.1 Codex Mini" },
				{ id: "gpt-4o-mini", name: "GPT 4o Mini" },
			],
		});
	}),

	http.get("/api/api-keys/", () => {
		return HttpResponse.json(state.apiKeys);
	}),

	http.post("/api/api-keys/", async ({ request }) => {
		const payload = await parseJsonBody(request, ApiKeyCreatePayloadSchema);
		const sequence = state.apiKeys.length + 1;
		const created = createApiKeyCreateResponse({
			...createApiKey({
				id: `key_${sequence}`,
				name: payload?.name ?? `API Key ${sequence}`,
			}),
			key: `sk-test-generated-${sequence}`,
		});
		state.apiKeys = [...state.apiKeys, createApiKey(created)];
		return HttpResponse.json(created);
	}),

	http.patch("/api/api-keys/:keyId", async ({ params, request }) => {
		const keyId = String(params.keyId);
		const existing = findApiKey(keyId);
		if (!existing) {
			return HttpResponse.json(
				{ error: { code: "not_found", message: "API key not found" } },
				{ status: 404 },
			);
		}
		const payload = await parseJsonBody(request, ApiKeyUpdatePayloadSchema);
		if (!payload) {
			return HttpResponse.json(existing);
		}

		// Build override with converted limits (create format → response format)
		const overrides: Partial<ApiKey> = {
			...(payload.name !== undefined ? { name: payload.name } : {}),
			...(payload.allowedModels !== undefined
				? { allowedModels: payload.allowedModels }
				: {}),
			...(payload.isActive !== undefined ? { isActive: payload.isActive } : {}),
			...(payload.assignedAccountIds !== undefined
				? { accountAssignmentScopeEnabled: payload.assignedAccountIds.length > 0 }
				: {}),
			...(payload.assignedAccountIds !== undefined
				? { assignedAccountIds: payload.assignedAccountIds }
				: {}),
		};

		if (payload.limits) {
			overrides.limits = payload.limits.map((l, idx) => ({
				id: idx + 100,
				limitType: l.limitType,
				limitWindow: l.limitWindow,
				maxValue: l.maxValue,
				currentValue: 0,
				modelFilter: l.modelFilter ?? null,
				resetAt: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
			}));
		}

		const updated = createApiKey({
			...existing,
			...overrides,
			id: keyId,
		});
		state.apiKeys = state.apiKeys.map((item) =>
			item.id === keyId ? updated : item,
		);
		return HttpResponse.json(updated);
	}),

	http.delete("/api/api-keys/:keyId", ({ params }) => {
		const keyId = String(params.keyId);
		const exists = state.apiKeys.some((item) => item.id === keyId);
		if (!exists) {
			return HttpResponse.json(
				{ error: { code: "not_found", message: "API key not found" } },
				{ status: 404 },
			);
		}
		state.apiKeys = state.apiKeys.filter((item) => item.id !== keyId);
		return new HttpResponse(null, { status: 204 });
	}),

	http.post("/api/api-keys/:keyId/regenerate", ({ params }) => {
		const keyId = String(params.keyId);
		const existing = findApiKey(keyId);
		if (!existing) {
			return HttpResponse.json(
				{ error: { code: "not_found", message: "API key not found" } },
				{ status: 404 },
			);
		}
		const regenerated = createApiKeyCreateResponse({
			...existing,
			key: `sk-test-regenerated-${keyId}`,
		});
		state.apiKeys = state.apiKeys.map((item) =>
			item.id === keyId ? createApiKey(regenerated) : item,
		);
		return HttpResponse.json(regenerated);
	}),

	http.get("/api/api-keys/:keyId/trends", ({ params }) => {
		const keyId = String(params.keyId);
		const existing = findApiKey(keyId);
		if (!existing) {
			return HttpResponse.json(
				{ error: { code: "not_found", message: "API key not found" } },
				{ status: 404 },
			);
		}
		return HttpResponse.json(createApiKeyTrends({ keyId }));
	}),

	http.get("/api/api-keys/:keyId/usage-7d", ({ params }) => {
		const keyId = String(params.keyId);
		const existing = findApiKey(keyId);
		if (!existing) {
			return HttpResponse.json(
				{ error: { code: "not_found", message: "API key not found" } },
				{ status: 404 },
			);
		}
		return HttpResponse.json(createApiKeyUsage7Day({ keyId }));
	}),

	http.get("/api/api-keys/:keyId/account-usage-7d", ({ params }) => {
		const keyId = String(params.keyId);
		const existing = findApiKey(keyId);
		if (!existing) {
			return HttpResponse.json(
				{ error: { code: "not_found", message: "API key not found" } },
				{ status: 404 },
			);
		}
		return HttpResponse.json(createApiKeyAccountUsage7Day({ keyId }));
	}),
];
