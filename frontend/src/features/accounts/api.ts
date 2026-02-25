import { del, get, post } from "@/lib/api-client";

import {
  AccountActionResponseSchema,
  AccountImportResponseSchema,
  AccountsResponseSchema,
  CredentialsImportResponseSchema,
  AccountTrendsResponseSchema,
  OauthCompleteRequestSchema,
  OauthCompleteResponseSchema,
  OauthStartRequestSchema,
  OauthStartResponseSchema,
  OauthStatusResponseSchema,
} from "@/features/accounts/schemas";

const ACCOUNTS_BASE_PATH = "/api/accounts";
const OAUTH_BASE_PATH = "/api/oauth";

export function listAccounts(ownerUserId?: string) {
  const query = new URLSearchParams();
  if (ownerUserId) {
    query.set("ownerUserId", ownerUserId);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`${ACCOUNTS_BASE_PATH}${suffix}`, AccountsResponseSchema);
}

export function importAccount(file: File) {
  const formData = new FormData();
  formData.append("auth_json", file);
  return post(`${ACCOUNTS_BASE_PATH}/import`, AccountImportResponseSchema, {
    body: formData,
  });
}

export function importCredentials(credentialsText: string) {
  return post(`${ACCOUNTS_BASE_PATH}/import-credentials`, CredentialsImportResponseSchema, {
    body: {
      credentialsText,
    },
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

export function getAccountTrends(accountId: string) {
  return get(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}/trends`,
    AccountTrendsResponseSchema,
  );
}

export function deleteAccount(accountId: string) {
  return del(
    `${ACCOUNTS_BASE_PATH}/${encodeURIComponent(accountId)}`,
    AccountActionResponseSchema,
  );
}

export function startOauth(payload: unknown) {
  const validated = OauthStartRequestSchema.parse(payload);
  return post(`${OAUTH_BASE_PATH}/start`, OauthStartResponseSchema, {
    body: validated,
  });
}

export function getOauthStatus() {
  return get(`${OAUTH_BASE_PATH}/status`, OauthStatusResponseSchema);
}

export function completeOauth(payload?: unknown) {
  const validated = OauthCompleteRequestSchema.parse(payload ?? {});
  return post(`${OAUTH_BASE_PATH}/complete`, OauthCompleteResponseSchema, {
    body: validated,
  });
}
