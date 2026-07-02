import { patch, post, get } from "@/lib/api-client";

import {
  AddClaudeAccountRequestSchema,
  ClaudeAccountsResponseSchema,
  ClaudeAccountSchema,
  DisableClaudeAccountRequestSchema,
  type AddClaudeAccountRequest,
  type ClaudeAccount,
} from "@/features/claude/schemas";

const CLAUDE_BASE_PATH = "/api/claude/accounts";

export function listClaudeAccounts() {
  return get(CLAUDE_BASE_PATH, ClaudeAccountsResponseSchema);
}

export function addClaudeAccount(payload: AddClaudeAccountRequest) {
  const validated = AddClaudeAccountRequestSchema.parse(payload);
  return post(CLAUDE_BASE_PATH, ClaudeAccountSchema, { body: validated });
}

export function disableClaudeAccount(accountId: string, reason?: string) {
  const validated = reason === undefined
    ? undefined
    : DisableClaudeAccountRequestSchema.parse({ reason });
  return patch(
    `${CLAUDE_BASE_PATH}/${encodeURIComponent(accountId)}/disable`,
    null,
    validated ? { body: validated } : undefined,
  );
}

export function enableClaudeAccount(accountId: string) {
  return patch(
    `${CLAUDE_BASE_PATH}/${encodeURIComponent(accountId)}/enable`,
    null,
  );
}

export type { ClaudeAccount };