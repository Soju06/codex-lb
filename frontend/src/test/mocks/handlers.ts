import { http, HttpResponse } from "msw";

import {
  createAccountSummary,
  createApiKey,
  createApiKeyCreateResponse,
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
  type AccountSummary,
  type ApiKey,
  type DashboardAuthSession,
  type DashboardSettings,
  type RequestLogEntry,
} from "@/test/mocks/factories";

const MODEL_OPTION_DELIMITER = ":::";
const STATUS_ORDER = ["ok", "rate_limit", "quota", "error"] as const;

const state: {
  accounts: AccountSummary[];
  requestLogs: RequestLogEntry[];
  authSession: DashboardAuthSession;
  settings: DashboardSettings;
  apiKeys: ApiKey[];
} = {
  accounts: createDefaultAccounts(),
  requestLogs: createDefaultRequestLogs(),
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

function filterRequestLogs(url: URL): RequestLogEntry[] {
  const accountIds = new Set(url.searchParams.getAll("accountId"));
  const statuses = new Set(url.searchParams.getAll("status").map((value) => value.toLowerCase()));
  const models = new Set(url.searchParams.getAll("model"));
  const reasoningEfforts = new Set(url.searchParams.getAll("reasoningEffort"));
  const modelOptions = new Set(url.searchParams.getAll("modelOption"));
  const search = (url.searchParams.get("search") || "").trim().toLowerCase();
  const since = parseDateValue(url.searchParams.get("since"));
  const until = parseDateValue(url.searchParams.get("until"));

  return state.requestLogs.filter((entry) => {
    if (accountIds.size > 0 && !accountIds.has(entry.accountId)) {
      return false;
    }

    if (statuses.size > 0 && !statuses.has("all") && !statuses.has(entry.status)) {
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
  const modelOptions = [...modelMap.values()].sort((a, b) => {
    if (a.model !== b.model) {
      return a.model.localeCompare(b.model);
    }
    return (a.reasoningEffort ?? "").localeCompare(b.reasoningEffort ?? "");
  });

  const presentStatuses = new Set(entries.map((entry) => entry.status));
  const statuses = STATUS_ORDER.filter((status) => presentStatuses.has(status));

  return createRequestLogFilterOptions({
    accountIds,
    modelOptions,
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
    const limit = Number.isFinite(limitRaw) && limitRaw > 0 ? Math.floor(limitRaw) : 50;
    const offset = Number.isFinite(offsetRaw) && offsetRaw > 0 ? Math.floor(offsetRaw) : 0;
    const requests = filtered.slice(offset, offset + limit);
    return HttpResponse.json(createRequestLogsResponse(requests, total, offset + limit < total));
  }),

  http.get("/api/request-logs/options", ({ request }) => {
    const filtered = filterRequestLogs(new URL(request.url));
    return HttpResponse.json(requestLogOptionsFromEntries(filtered));
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
    const payload = (await request.json().catch(() => ({}))) as { forceMethod?: string };
    if (payload.forceMethod === "device") {
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
    const payload = (await request.json().catch(() => null)) as Partial<DashboardSettings> | null;
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

  http.get("/api/api-keys/", () => {
    return HttpResponse.json(state.apiKeys);
  }),

  http.post("/api/api-keys/", async ({ request }) => {
    const payload = (await request.json().catch(() => ({}))) as Partial<ApiKey>;
    const sequence = state.apiKeys.length + 1;
    const created = createApiKeyCreateResponse({
      ...createApiKey({
        id: `key_${sequence}`,
        name: payload.name ?? `API Key ${sequence}`,
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
    const payload = (await request.json().catch(() => ({}))) as Partial<ApiKey>;
    const updated = createApiKey({
      ...existing,
      ...payload,
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
