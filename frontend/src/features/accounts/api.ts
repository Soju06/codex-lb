import { del, get, handleUnauthorizedResponse, patch, post, put } from "@/lib/api-client";

import {
  AccountActionResponseSchema,
  AccountAliasRequestSchema,
  AccountAliasResponseSchema,
  AccountAuthExportResponseSchema,
  AccountBundleCommitResponseSchema,
  AccountBundlePreflightResponseSchema,
  AccountImportResponseSchema,
  AccountLimitWarmupUpdateRequestSchema,
  AccountLimitWarmupUpdateResponseSchema,
  AccountUpdateRequestSchema,
  AccountsResponseSchema,
  AccountRoutingPolicyUpdateRequestSchema,
  AccountRoutingPolicyUpdateResponseSchema,
  AccountUsageResetConsumeRequestSchema,
  AccountUsageResetConsumeResponseSchema,
  AccountUsageResetCreditsResponseSchema,
  AccountTrendsResponseSchema,
  AccountProbeRequestSchema,
  AccountProbeResponseSchema,
  ConsumeRateLimitResetCreditResponseSchema,
  ManualOauthCallbackRequestSchema,
  ManualOauthCallbackResponseSchema,
  OauthCompleteRequestSchema,
  OauthCompleteResponseSchema,
  OauthStartRequestSchema,
  OauthStartResponseSchema,
  OauthStatusResponseSchema,
  RateLimitResetCreditsSnapshotSchema,
  RuntimeConnectAddressResponseSchema,
} from "@/features/accounts/schemas";
import type {
  AccountRoutingPolicy,
  AccountUsageResetConsumeRequest,
} from "@/features/accounts/schemas";

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

export async function exportAccountBundle(accountIds: string[] | null, passphrase: string) {
  const response = await fetch(`${ACCOUNTS_BASE_PATH}/bundle/export`, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accountIds, passphrase }),
  });
  handleUnauthorizedResponse(response);
  if (!response.ok) {
    throw new Error(await safeBundleError(response));
  }
  return response.blob();
}

export function preflightAccountBundle(file: File, passphrase: string) {
  const formData = new FormData();
  formData.append("bundle", file);
  formData.append("passphrase", passphrase);
  return post(`${ACCOUNTS_BASE_PATH}/bundle/import/preflight`, AccountBundlePreflightResponseSchema, {
    body: formData,
    cache: "no-store",
  });
}

export function commitAccountBundle(params: {
  file: File;
  passphrase: string;
  integrityToken: string;
  conflictMode: "skip" | "replace";
  confirmReplace: boolean;
}) {
  const formData = new FormData();
  formData.append("bundle", params.file);
  formData.append("passphrase", params.passphrase);
  formData.append("integrity_token", params.integrityToken);
  formData.append("conflict_mode", params.conflictMode);
  formData.append("confirm_replace", String(params.confirmReplace));
  return post(`${ACCOUNTS_BASE_PATH}/bundle/import/commit`, AccountBundleCommitResponseSchema, {
    body: formData,
    cache: "no-store",
  });
}

async function safeBundleError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { error?: { message?: unknown } };
    return typeof payload.error?.message === "string" ? payload.error.message : "Account bundle request failed";
  } catch {
    return "Account bundle request failed";
  }
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

export function getAccountUsageResetCredits(accountId: string) {
  return get(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/usage-reset-credits`,
    AccountUsageResetCreditsResponseSchema,
  );
}

export function consumeAccountUsageResetCredit(
  accountId: string,
  payload?: AccountUsageResetConsumeRequest,
) {
  const validated = payload === undefined ? undefined : AccountUsageResetConsumeRequestSchema.parse(payload);
  return post(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/usage-reset-credits/consume`,
    AccountUsageResetConsumeResponseSchema,
    validated ? { body: validated } : undefined,
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

export function getRateLimitResetCredits(accountId: string) {
  return get(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/rate-limit-reset-credits`,
    RateLimitResetCreditsSnapshotSchema.nullable(),
  );
}

export function consumeRateLimitResetCredit(
  accountId: string,
  payload?: AccountUsageResetConsumeRequest,
) {
  const validated = payload === undefined ? undefined : AccountUsageResetConsumeRequestSchema.parse(payload);
  return post(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/rate-limit-reset-credits/consume`,
    ConsumeRateLimitResetCreditResponseSchema,
    validated ? { body: validated } : undefined,
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
