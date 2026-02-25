import { http, HttpResponse } from "msw";
import { z } from "zod";

import { LIMIT_TYPES, LIMIT_WINDOWS } from "@/features/api-keys/schemas";
import {
  createAccountSummary,
  createAccountTrends,
  createApiKey,
  createApiKeyCreateResponse,
  createDashboardAuthSession,
  createDashboardOverview,
  createDashboardSettings,
  createDefaultAccounts,
  createDefaultDashboardUsers,
  createDefaultApiKeys,
  createDefaultRequestLogs,
  createOauthCompleteResponse,
  createOauthStartResponse,
  createOauthStatusResponse,
  createRequestLogFilterOptions,
  createRequestLogsResponse,
  type AccountSummary,
  type ApiKey,
  type DashboardUser,
  type DashboardAuthSession,
  type DashboardSettings,
  type RequestLogEntry,
} from "@/test/mocks/factories";

const MODEL_OPTION_DELIMITER = ":::";
const STATUS_ORDER = ["ok", "rate_limit", "quota", "error"] as const;

// ── Zod schemas for mock request bodies ──

const OauthStartPayloadSchema = z.object({
  forceMethod: z.string().optional(),
}).passthrough();

const CredentialsImportPayloadSchema = z.object({
  credentialsText: z.string(),
}).passthrough();

const ApiKeyCreatePayloadSchema = z.object({
  name: z.string().optional(),
  ownerUserId: z.string().optional(),
}).passthrough();

const ApiKeyUpdatePayloadSchema = z.object({
  name: z.string().optional(),
  allowedModels: z.array(z.string()).nullable().optional(),
  isActive: z.boolean().optional(),
  resetUsage: z.boolean().optional(),
  limits: z.array(
    z.object({
      limitType: z.enum(LIMIT_TYPES),
      limitWindow: z.enum(LIMIT_WINDOWS),
      maxValue: z.number(),
      modelFilter: z.string().nullable().optional(),
    }),
  ).optional(),
}).passthrough();

const SettingsPayloadSchema = z.object({
  stickyThreadsEnabled: z.boolean().optional(),
  preferEarlierResetAccounts: z.boolean().optional(),
  importWithoutOverwrite: z.boolean().optional(),
  totpRequiredOnLogin: z.boolean().optional(),
  totpConfigured: z.boolean().optional(),
  apiKeyAuthEnabled: z.boolean().optional(),
}).passthrough();

// ── Helpers ──

async function parseJsonBody<T>(request: Request, schema: z.ZodType<T>): Promise<T | null> {
  try {
    const raw: unknown = await request.json();
    const result = schema.safeParse(raw);
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}

const state: {
  accounts: AccountSummary[];
  requestLogs: RequestLogEntry[];
  users: DashboardUser[];
  authSession: DashboardAuthSession;
  settings: DashboardSettings;
  apiKeys: ApiKey[];
} = {
  accounts: createDefaultAccounts().map((account, index) => ({
    ...account,
    ownerUserId: index === 0 ? "dashboard-user-admin-default" : "dashboard-user-1",
  })),
  requestLogs: createDefaultRequestLogs(),
  users: createDefaultDashboardUsers(),
  authSession: createDashboardAuthSession(),
  settings: createDashboardSettings(),
  apiKeys: createDefaultApiKeys(),
};

function parseDateValue(value: string | null): number | null {
  if (!value) {
    return null;
  }
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? null : timestamp;
}

function filterRequestLogs(url: URL, options?: { includeStatuses?: boolean }): RequestLogEntry[] {
  const includeStatuses = options?.includeStatuses ?? true;
  const ownerUserId = url.searchParams.get("ownerUserId");
  const accountIds = new Set(url.searchParams.getAll("accountId"));
  const statuses = new Set(url.searchParams.getAll("status").map((value) => value.toLowerCase()));
  const models = new Set(url.searchParams.getAll("model"));
  const reasoningEfforts = new Set(url.searchParams.getAll("reasoningEffort"));
  const modelOptions = new Set(url.searchParams.getAll("modelOption"));
  const search = (url.searchParams.get("search") || "").trim().toLowerCase();
  const since = parseDateValue(url.searchParams.get("since"));
  const until = parseDateValue(url.searchParams.get("until"));

  return state.requestLogs.filter((entry) => {
    const accountOwner = state.accounts.find((account) => account.accountId === entry.accountId)?.ownerUserId;
    if (ownerUserId && accountOwner !== ownerUserId) {
      return false;
    }

    if (accountIds.size > 0 && !accountIds.has(entry.accountId)) {
      return false;
    }

    if (includeStatuses && statuses.size > 0 && !statuses.has("all") && !statuses.has(entry.status)) {
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

function requestLogOptionsFromEntries(entries: RequestLogEntry[]) {
  const accountIds = [...new Set(entries.map((entry) => entry.accountId))].sort();

  const modelMap = new Map<string, { model: string; reasoningEffort: string | null }>();
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

  const presentStatuses = new Set(entries.map((entry) => entry.status));
  const statuses = STATUS_ORDER.filter((status) => presentStatuses.has(status));

  return createRequestLogFilterOptions({
    accountIds,
    modelOptions: modelOptionsList,
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

  http.get("/api/dashboard/overview", ({ request }) => {
    const url = new URL(request.url);
    const ownerUserId = url.searchParams.get("ownerUserId");
    const scopedAccounts = ownerUserId
      ? state.accounts.filter((account) => account.ownerUserId === ownerUserId)
      : state.accounts;
    return HttpResponse.json(
      createDashboardOverview({
        accounts: scopedAccounts,
      }),
    );
  }),

  http.get("/api/request-logs", ({ request }) => {
    const url = new URL(request.url);
    const filtered = filterRequestLogs(url);
    const total = filtered.length;
    const limitRaw = Number(url.searchParams.get("limit") ?? 50);
    const offsetRaw = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number.isFinite(limitRaw) && limitRaw > 0 ? Math.floor(limitRaw) : 50;
    const offset = Number.isFinite(offsetRaw) && offsetRaw > 0 ? Math.floor(offsetRaw) : 0;
    const requests = filtered.slice(offset, offset + limit);
    return HttpResponse.json(createRequestLogsResponse(requests, total, offset + limit < total));
  }),

  http.get("/api/request-logs/options", ({ request }) => {
    const filtered = filterRequestLogs(new URL(request.url), { includeStatuses: false });
    return HttpResponse.json(requestLogOptionsFromEntries(filtered));
  }),

  http.get("/api/accounts", ({ request }) => {
    const url = new URL(request.url);
    const ownerUserId = url.searchParams.get("ownerUserId");
    const scopedAccounts = ownerUserId
      ? state.accounts.filter((account) => account.ownerUserId === ownerUserId)
      : state.accounts;
    return HttpResponse.json({ accounts: scopedAccounts });
  }),

  http.post("/api/accounts/import", async () => {
    const sequence = state.accounts.length + 1;
    const ownerUserId = state.authSession.user?.id ?? "dashboard-user-admin-default";
    const created = createAccountSummary({
      accountId: `acc_imported_${sequence}`,
      email: `imported-${sequence}@example.com`,
      displayName: `imported-${sequence}@example.com`,
      status: "active",
      ownerUserId,
    });
    state.accounts = [...state.accounts, created];
    return HttpResponse.json({
      accountId: created.accountId,
      email: created.email,
      planType: created.planType,
      status: created.status,
    });
  }),

  http.post("/api/accounts/import-credentials", async ({ request }) => {
    const payload = await parseJsonBody(request, CredentialsImportPayloadSchema);
    const lines = payload?.credentialsText
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0) ?? [];

    const ownerUserId = state.authSession.user?.id ?? "dashboard-user-admin-default";
    const results = lines.map((line, index) => {
      const sequence = state.accounts.length + 1;
      const [email = `imported-${sequence}@example.com`] = line.split(":");
      const created = createAccountSummary({
        accountId: `acc_imported_credentials_${sequence}`,
        email,
        displayName: email,
        status: "active",
        ownerUserId,
      });
      state.accounts = [...state.accounts, created];
      return {
        line: index + 1,
        email,
        status: "imported",
        accountId: created.accountId,
        error: null,
      };
    });

    return HttpResponse.json({
      total: results.length,
      imported: results.length,
      failed: 0,
      results,
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
    const exists = state.accounts.some((account) => account.accountId === accountId);
    if (!exists) {
      return HttpResponse.json(
        { error: { code: "account_not_found", message: "Account not found" } },
        { status: 404 },
      );
    }
    state.accounts = state.accounts.filter((account) => account.accountId !== accountId);
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

  http.post("/api/dashboard-auth/password/login", async ({ request }) => {
    const payload = await parseJsonBody(
      request,
      z.object({
        username: z.string().min(1),
        password: z.string().min(1),
      }),
    );
    const normalizedUsername = payload?.username.trim().toLowerCase();
    const user = state.users.find((item) => item.username === normalizedUsername) ?? state.users[0];
    state.authSession = createDashboardAuthSession({
      ...state.authSession,
      authenticated: !state.authSession.totpRequiredOnLogin,
      user: user
        ? {
            id: user.id,
            username: user.username,
            role: user.role,
          }
        : null,
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
      user: null,
    });
    return HttpResponse.json({ status: "ok" });
  }),

  http.get("/api/users", ({ request }) => {
    const url = new URL(request.url);
    const search = (url.searchParams.get("search") || "").trim().toLowerCase();
    const role = (url.searchParams.get("role") || "").trim().toLowerCase();
    const users = state.users.filter((user) => {
      if (search && !user.username.toLowerCase().includes(search)) {
        return false;
      }
      if (role && user.role !== role) {
        return false;
      }
      return true;
    });
    return HttpResponse.json({ users });
  }),

  http.post("/api/users", async ({ request }) => {
    const payload = await parseJsonBody(
      request,
      z.object({
        username: z.string().min(1),
        password: z.string().min(8),
        role: z.enum(["admin", "user"]),
      }),
    );
    if (!payload) {
      return HttpResponse.json({ error: { code: "invalid_payload", message: "Invalid payload" } }, { status: 400 });
    }
    const id = `dashboard-user-${state.users.length + 1}`;
    const created = {
      id,
      username: payload.username.trim().toLowerCase(),
      role: payload.role,
      isActive: true,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    } satisfies DashboardUser;
    state.users = [...state.users, created];
    return HttpResponse.json(created);
  }),

  http.patch("/api/users/:userId", async ({ params, request }) => {
    const userId = String(params.userId);
    const existing = state.users.find((user) => user.id === userId);
    if (!existing) {
      return HttpResponse.json({ error: { code: "user_not_found", message: "User not found" } }, { status: 404 });
    }
    const payload = await parseJsonBody(
      request,
      z.object({
        username: z.string().min(1).optional(),
        password: z.string().min(8).optional(),
        role: z.enum(["admin", "user"]).optional(),
        isActive: z.boolean().optional(),
      }),
    );
    if (!payload) {
      return HttpResponse.json({ error: { code: "invalid_payload", message: "Invalid payload" } }, { status: 400 });
    }
    const updated: DashboardUser = {
      ...existing,
      ...(payload.username ? { username: payload.username.trim().toLowerCase() } : {}),
      ...(payload.role ? { role: payload.role } : {}),
      ...(typeof payload.isActive === "boolean" ? { isActive: payload.isActive } : {}),
      updatedAt: new Date().toISOString(),
    };
    state.users = state.users.map((user) => (user.id === userId ? updated : user));
    if (state.authSession.user?.id === userId) {
      state.authSession = createDashboardAuthSession({
        ...state.authSession,
        user: {
          id: updated.id,
          username: updated.username,
          role: updated.role,
        },
      });
    }
    return HttpResponse.json(updated);
  }),

  http.delete("/api/users/:userId", ({ params }) => {
    const userId = String(params.userId);
    const exists = state.users.some((user) => user.id === userId);
    if (!exists) {
      return HttpResponse.json({ error: { code: "user_not_found", message: "User not found" } }, { status: 404 });
    }
    state.users = state.users.filter((user) => user.id !== userId);
    return HttpResponse.json({ status: "deleted" });
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

  http.get("/api/api-keys/", ({ request }) => {
    const url = new URL(request.url);
    const ownerUserId = url.searchParams.get("ownerUserId");
    const scoped = ownerUserId
      ? state.apiKeys.filter((item) => item.ownerUserId === ownerUserId)
      : state.apiKeys;
    return HttpResponse.json(scoped);
  }),

  http.post("/api/api-keys/", async ({ request }) => {
    const payload = await parseJsonBody(request, ApiKeyCreatePayloadSchema);
    const sequence = state.apiKeys.length + 1;
    const ownerUserId = payload?.ownerUserId ?? state.authSession.user?.id ?? "dashboard-user-admin-default";
    const created = createApiKeyCreateResponse({
      ...createApiKey({
        id: `key_${sequence}`,
        name: payload?.name ?? `API Key ${sequence}`,
        ownerUserId,
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
      return HttpResponse.json({ error: { code: "not_found", message: "API key not found" } }, { status: 404 });
    }
    const payload = await parseJsonBody(request, ApiKeyUpdatePayloadSchema);
    if (!payload) {
      return HttpResponse.json(existing);
    }

    // Build override with converted limits (create format → response format)
    const overrides: Partial<ApiKey> = {
      ...(payload.name !== undefined ? { name: payload.name } : {}),
      ...(payload.allowedModels !== undefined ? { allowedModels: payload.allowedModels } : {}),
      ...(payload.isActive !== undefined ? { isActive: payload.isActive } : {}),
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
    state.apiKeys = state.apiKeys.map((item) => (item.id === keyId ? updated : item));
    return HttpResponse.json(updated);
  }),

  http.delete("/api/api-keys/:keyId", ({ params }) => {
    const keyId = String(params.keyId);
    const exists = state.apiKeys.some((item) => item.id === keyId);
    if (!exists) {
      return HttpResponse.json({ error: { code: "not_found", message: "API key not found" } }, { status: 404 });
    }
    state.apiKeys = state.apiKeys.filter((item) => item.id !== keyId);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/api-keys/:keyId/regenerate", ({ params }) => {
    const keyId = String(params.keyId);
    const existing = findApiKey(keyId);
    if (!existing) {
      return HttpResponse.json({ error: { code: "not_found", message: "API key not found" } }, { status: 404 });
    }
    const regenerated = createApiKeyCreateResponse({
      ...existing,
      key: `sk-test-regenerated-${keyId}`,
    });
    state.apiKeys = state.apiKeys.map((item) => (item.id === keyId ? createApiKey(regenerated) : item));
    return HttpResponse.json(regenerated);
  }),
];
