import { del, get, patch, post, put } from "@/lib/api-client";

import {
  AccountActionResponseSchema,
  AccountAliasRequestSchema,
  AccountAliasResponseSchema,
  AccountAuthExportResponseSchema,
  AccountImportResponseSchema,
  AccountLimitWarmupUpdateRequestSchema,
  AccountLimitWarmupUpdateResponseSchema,
  AccountUpdateRequestSchema,
  AccountsResponseSchema,
  AccountRoutingPolicyUpdateRequestSchema,
  AccountRoutingPolicyUpdateResponseSchema,
  AccountTrendsResponseSchema,
  AccountProbeRequestSchema,
  AccountProbeResponseSchema,
  ManualOauthCallbackRequestSchema,
  ManualOauthCallbackResponseSchema,
  OauthCompleteRequestSchema,
  OauthCompleteResponseSchema,
  OauthStartRequestSchema,
  OauthStartResponseSchema,
  OauthStatusResponseSchema,
  RuntimeConnectAddressResponseSchema,
} from "@/features/accounts/schemas";
import type { AccountRoutingPolicy } from "@/features/accounts/schemas";

const ACCOUNTS_BASE_PATH = "/api/accounts";
const OAUTH_BASE_PATH = "/api/oauth";

export function listAccounts() {
  return get(ACCOUNTS_BASE_PATH, AccountsResponseSchema);
}

export function importAccount(file: File) {
  const formData = new FormData();
  formData.append("auth_json", file);
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

export function updateAccount(accountId: string, payload: unknown) {
  const validated = AccountUpdateRequestSchema.parse(payload);
  return patch(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}`,
    AccountActionResponseSchema,
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

export function updateAccountRoutingPolicy(
  accountId: string,
  routingPolicy: AccountRoutingPolicy,
) {
  const payload = AccountRoutingPolicyUpdateRequestSchema.parse({ routingPolicy });
  return put(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/routing-policy`,
    AccountRoutingPolicyUpdateResponseSchema,
    { body: payload },
  );
}

export function getAccountTrends(accountId: string) {
  return get(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/trends`,
    AccountTrendsResponseSchema,
  );
}

export function probeAccount(accountId: string, payload?: unknown) {
  const validated = payload === undefined ? undefined : AccountProbeRequestSchema.parse(payload);
  return post(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/probe`,
    AccountProbeResponseSchema,
    validated ? { body: validated } : undefined,
  );
}

export function exportAccountAuth(accountId: string) {
  return post(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/export/auth`,
    AccountAuthExportResponseSchema,
  );
}

export function deleteAccount(accountId: string, deleteHistory = false) {
  const qs = deleteHistory ? "?delete_history=true" : "";
  return del(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}${qs}`,
    AccountActionResponseSchema,
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
