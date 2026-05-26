import { del, get, post, put } from "@/lib/api-client";

import {
  AccountActionResponseSchema,
  AccountAliasRequestSchema,
  AccountAliasResponseSchema,
  AccountExportResponseSchema,
  AccountOpenCodeAuthExportResponseSchema,
  AccountImportResponseSchema,
  AccountLimitWarmupUpdateRequestSchema,
  AccountLimitWarmupUpdateResponseSchema,
  AccountProxyClearResponseSchema,
  AccountProxyInputSchema,
  AccountProxySummarySchema,
  AccountsResponseSchema,
  AccountTrendsResponseSchema,
  ManualOauthCallbackRequestSchema,
  ManualOauthCallbackResponseSchema,
  OauthCompleteRequestSchema,
  OauthCompleteResponseSchema,
  OauthResetResponseSchema,
  OauthStartRequestSchema,
  OauthStartResponseSchema,
  OauthStatusResponseSchema,
  RuntimeConnectAddressResponseSchema,
} from "@/features/accounts/schemas";
import type { AccountProxyInput } from "@/features/accounts/schemas";

const ACCOUNTS_BASE_PATH = "/api/accounts";
const OAUTH_BASE_PATH = "/api/oauth";

export function listAccounts() {
  return get(ACCOUNTS_BASE_PATH, AccountsResponseSchema);
}

export type ImportAccountVariables = {
  file: File;
  proxy?: AccountProxyInput;
};

export function importAccount({ file, proxy }: ImportAccountVariables) {
  const formData = new FormData();
  formData.append("auth_json", file);
  if (proxy) {
    formData.append("proxyHost", proxy.host);
    formData.append("proxyPort", String(proxy.port));
    if (proxy.username) formData.append("proxyUsername", proxy.username);
    if (proxy.password) formData.append("proxyPassword", proxy.password);
    formData.append("proxyRemoteDns", String(proxy.remoteDns));
    if (proxy.label) formData.append("proxyLabel", proxy.label);
  }
  return post(`${ACCOUNTS_BASE_PATH}/import`, AccountImportResponseSchema, {
    body: formData,
  });
}

export function pauseAccount(accountId: string) {
  return post(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/pause`,
    AccountActionResponseSchema,
  );
}

export function reactivateAccount(accountId: string) {
  return post(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/reactivate`,
    AccountActionResponseSchema,
  );
}

export function setAccountAlias(accountId: string, alias: string | null) {
  const validated = AccountAliasRequestSchema.parse({ alias });
  return put(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/alias`,
    AccountAliasResponseSchema,
    { body: validated },
  );
}

export function updateAccountLimitWarmup(accountId: string, enabled: boolean) {
  const payload = AccountLimitWarmupUpdateRequestSchema.parse({ enabled });
  return put(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/limit-warmup`,
    AccountLimitWarmupUpdateResponseSchema,
    { body: payload },
  );
}

export function getAccountTrends(accountId: string) {
  return get(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/trends`,
    AccountTrendsResponseSchema,
  );
}

export function exportAccountOpenCodeAuth(accountId: string) {
  return post(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/export/opencode-auth`,
    AccountOpenCodeAuthExportResponseSchema,
  );
}

export function deleteAccount(accountId: string, deleteHistory = false) {
  const qs = deleteHistory ? "?delete_history=true" : "";
  return del(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}${qs}`,
    AccountActionResponseSchema,
  );
}

export function exportAccount(accountId: string) {
  return post(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/export`,
    AccountExportResponseSchema,
    { cache: "no-store" },
  );
}

export function setAccountProxy(accountId: string, payload: unknown) {
  const validated = AccountProxyInputSchema.parse(payload);
  return post(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/proxy`,
    AccountProxySummarySchema,
    { body: validated },
  );
}

export function clearAccountProxy(accountId: string) {
  return del(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/proxy`,
    AccountProxyClearResponseSchema,
  );
}

export function startOauth(payload: unknown) {
  const validated = OauthStartRequestSchema.parse(payload);
  return post(`${OAUTH_BASE_PATH}/start`, OauthStartResponseSchema, {
    body: validated,
  });
}

export function getOauthStatus(flowId?: string) {
  const query = flowId ? `?flowId=${encodeURIComponent(flowId)}` : "";
  return get(`${OAUTH_BASE_PATH}/status${query}`, OauthStatusResponseSchema);
}

export function resetOauth() {
  return post(`${OAUTH_BASE_PATH}/reset`, OauthResetResponseSchema);
}

export function completeOauth(payload?: unknown) {
  const validated = OauthCompleteRequestSchema.parse(payload ?? {});
  return post(`${OAUTH_BASE_PATH}/complete`, OauthCompleteResponseSchema, {
    body: validated,
  });
}
export function submitManualOauthCallback(payload: unknown) {
  const validated = ManualOauthCallbackRequestSchema.parse(payload);
  return post(`${OAUTH_BASE_PATH}/manual-callback`, ManualOauthCallbackResponseSchema, {
    body: validated,
  });
}

export function getRuntimeConnectAddress() {
  return get("/api/settings/runtime/connect-address", RuntimeConnectAddressResponseSchema);
}
