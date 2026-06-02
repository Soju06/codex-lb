# unified-auth-export Specification (Delta)

## ADDED Requirements

### Requirement: Combined auth export endpoint

The system SHALL expose `POST /api/accounts/{id}/export/auth` that returns structured token data alongside both Codex and OpenCode auth.json format payloads in a single response.

#### Scenario: Successful combined export

- **GIVEN** an account with id `acct-123` exists with encrypted access, refresh, and id tokens
- **WHEN** a client calls `POST /api/accounts/acct-123/export/auth`
- **THEN** the response includes `account` with `email`, `accountId`, and `chatgptAccountId`
- **AND** `tokens` includes decrypted `idToken`, `accessToken`, `refreshToken`, and `expiresAtMs`
- **AND** `codexAuthJson` contains an object with Codex keys `auth_mode: "chatgpt"`, `OPENAI_API_KEY`, `tokens.id_token`, `tokens.access_token`, `tokens.refresh_token`, `tokens.account_id`, and `last_refresh`
- **AND** `opencodeAuthJson` contains an object with `openai.type: "oauth"`, `refresh`, `access`, `expires`, `accountId`
- **AND** `filename` contains a sanitized filename for download
- **AND** the response includes `Cache-Control: no-store` headers
- **AND** an `account_auth_exported` audit event is logged

#### Scenario: Account not found

- **GIVEN** no account exists with id `nonexistent`
- **WHEN** a client calls `POST /api/accounts/nonexistent/export/auth`
- **THEN** the response is `404` with error code `account_not_found`

### Requirement: Unified auth export modal with format selector

The frontend SHALL render a single "Export" button on the AccountActions component. Clicking it SHALL open a modal titled "Auth Export" containing a mode dropdown selector ("codex" / "opencode", default "codex") and a Download button. The displayed auth.json content SHALL update immediately when the dropdown selection changes without making a new API request.

#### Scenario: Codex mode shows truncated token previews

- **GIVEN** the "Auth Export" modal is open and "codex" is selected in the mode dropdown
- **THEN** the modal shows truncated `id_token`, `access_token`, and `refresh_token` previews with individual Copy buttons that copy the full token value
- **AND** the auth.json block displays the codex-format JSON content
- **AND** the Download button downloads the codex-format file

#### Scenario: OpenCode mode keeps existing behavior

- **GIVEN** the "Auth Export" modal is open and "opencode" is selected in the mode dropdown
- **THEN** the modal shows truncated `access_token` and `refresh_token` previews with individual Copy buttons
- **AND** the auth.json block displays the OpenCode-format JSON content
- **AND** the Download button downloads the OpenCode-format file

#### Scenario: Switching modes updates display without re-fetch

- **GIVEN** the "Auth Export" modal is open and displaying codex-format data
- **WHEN** the user selects "opencode" from the mode dropdown
- **THEN** the token previews and auth.json block switch to OpenCode format immediately
- **AND** no additional API request is made

### Requirement: Security warning on auth export

The Auth Export modal SHALL display a warning: "This payload contains raw access and refresh tokens. Store it only on machines you trust."

#### Scenario: Warning visible in both modes

- **GIVEN** the "Auth Export" modal is open
- **WHEN** the user switches between "codex" and "opencode" modes
- **THEN** the security warning remains visible regardless of mode
