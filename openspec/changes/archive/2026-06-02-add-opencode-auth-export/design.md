# Design

## Context
OpenCode official auth storage is provider-keyed JSON under `~/.local/share/opencode/auth.json`. For OpenAI OAuth, the source schema in `packages/opencode/src/auth/index.ts` defines:

```json
{
  "openai": {
    "type": "oauth",
    "refresh": "...",
    "access": "...",
    "expires": 1778372697615,
    "accountId": "..."
  }
}
```

The OpenCode `CodexAuthPlugin` uses the stored `access` token as `Authorization: Bearer <access>` and sets `ChatGPT-Account-Id` when `accountId` exists. If `access` is missing or `expires < Date.now()`, OpenCode refreshes with the stored `refresh` token.

codex-lb already stores `access_token_encrypted`, `refresh_token_encrypted`, `id_token_encrypted`, `chatgpt_account_id`, and `email` per account.

## Decisions

### 1) Export official OpenCode auth shape only
The downloadable JSON MUST be a complete official OpenCode `auth.json` object for a single account and MUST NOT include codex-lb metadata or custom multi-account fields. This keeps the exported file compatible with stock OpenCode.

### 2) Keep metadata outside the downloadable payload
The dashboard response MAY include metadata such as account email and filename, but the `authJson` payload itself is only the provider-keyed auth file.

### 3) Expiry handling
The service derives `expires` from the exported access token JWT `exp` claim in milliseconds. If the token cannot be parsed, it exports `expires: 0` so OpenCode immediately refreshes using the refresh token.

### 4) Auditing
The route logs `account_auth_exported` with account identity only. It MUST NOT log token values.

## Validation
- Backend integration test exports a selected account as official OpenCode auth JSON.
- Backend integration test verifies unknown accounts return 404.
- Frontend schema/component coverage verifies copy/download behavior and excludes metadata from the downloaded file.
