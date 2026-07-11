# claude-oauth-pool Specification (delta)

This delta corrects the OAuth endpoints used by the `Claude account add via OAuth`
requirement added in `add-claude-oauth-link`. The redirect URI and authorize
endpoint defaults shipped in that change were verified against secondary
sources but never against a live Anthropic account, and Anthropic rejects them
as "Redirect URI … is not supported by client." This delta pins defaults to
the values Anthropic actually accepts for the public Claude Code OAuth client
(`9d1c250a-e61b-44d9-88ed-5944d1962f5e`).

## MODIFIED Requirements

### Requirement: Claude account add via OAuth

The system SHALL expose `POST /api/claude/oauth/start`, `GET /api/claude/oauth/status`,
and `POST /api/claude/oauth/callback` to add a Claude account through an
authorization-code flow with PKCE and copy-paste code entry. The system SHALL:

- Generate an authorization URL with `response_type=code`, `code_challenge`
  (method `S256`), a server-generated `state` token, and a `code=true` query
  parameter as the first parameter, selecting Anthropic's OOB code-display
  flow.
- The default `claude_oauth_authorize_endpoint` SHALL be
  `https://claude.com/cai/oauth/authorize` and the default
  `claude_oauth_redirect_uri` SHALL be
  `https://platform.claude.com/oauth/code/callback`. These are the values
  Anthropic accepts for the public Claude Code OAuth client
  `9d1c250a-e61b-44d9-88ed-5944d1962f5e`. Operators MAY override either via
  env var (`CODEX_LB_CLAUDE_OAUTH_AUTHORIZE_ENDPOINT`,
  `CODEX_LB_CLAUDE_OAUTH_REDIRECT_URI`).
- Return the `state` token in the `/start` response payload (under a
  `stateToken` field) so the dashboard dialog can submit it unchanged on the
  `/callback` request.
- Require the user to paste the authorization `code` and the matching `state`
  back into `POST /api/claude/oauth/callback` to complete the flow.
- Exchange the code at the Anthropic OAuth token endpoint using the stored
  PKCE `code_verifier`, the configured `redirect_uri`, and the configured
  `client_id`.
- Parse the `id_token` from the token response. If `id_token` is missing or
  does not contain `claude_account_uuid`, the system SHALL reject the request
  with HTTP 400 and `error_code` `id_token_missing` or
  `id_token_claims_incomplete`, and SHALL direct the operator to use the
  manual paste endpoint.
- Persist the new account via the same encryption + insert path used by the
  existing `POST /api/claude/accounts` (manual paste) endpoint.
- Reject the callback when the supplied `state` does not match the stored
  state token (HTTP 400, `error_code: state_mismatch`).
- Reject the callback when the `code` is empty at request validation time
  (HTTP 422 Pydantic envelope).
- Reject a duplicate `claude_account_uuid` with HTTP 409
  (`error_code: account_already_exists`).
- Support at most one in-flight flow at a time. A new `POST /api/claude/oauth/start`
  SHALL transition any prior `pending` flow to `error` with
  `error_code: superseded`, and SHALL create a new flow with a fresh state
  token and PKCE pair.
- Consider a pending flow expired when
  `started_at + claude_oauth_flow_ttl_seconds < now()` at the moment of a
  status or callback request. Past that point the callback SHALL return HTTP
  410 (`error_code: flow_expired`) and the status endpoint SHALL report
  `status: error` with `error_code: flow_expired`.
- Surface Anthropic token-exchange errors (HTTP 400 `invalid_grant`, 5xx) as
  HTTP 502 with the original error code preserved (`invalid_grant`,
  `anthropic_unreachable`).
- Never log `access_token`, `refresh_token`, `id_token`, `code`,
  `code_verifier`, or `state` in plaintext.

#### Scenario: Authorization URL matches Anthropic's whitelist for the Claude Code client

- **GIVEN** the default `claude_oauth_authorize_endpoint` and `claude_oauth_redirect_uri` settings
- **WHEN** an operator calls `POST /api/claude/oauth/start`
- **THEN** the returned `authorization_url` starts with `https://claude.com/cai/oauth/authorize?code=true&`
- **AND** the `redirect_uri` query parameter equals `https://platform.claude.com/oauth/code/callback`
- **AND** Anthropic accepts the request (does not return "Redirect URI … is not supported by client")

#### Scenario: Operator override of redirect_uri takes precedence

- **GIVEN** an operator has set `CODEX_LB_CLAUDE_OAUTH_REDIRECT_URI=https://example.test/cb`
- **WHEN** an operator calls `POST /api/claude/oauth/start`
- **THEN** the returned `authorization_url` carries `redirect_uri=https%3A%2F%2Fexample.test%2Fcb`
- **AND** the default `https://platform.claude.com/oauth/code/callback` is NOT used

#### Scenario: Happy path (unchanged from `add-claude-oauth-link`)

- **WHEN** admin completes the OAuth flow and pastes the `code` and the matching `state`
- **THEN** the system returns 200 with the new account's public payload (no plaintext tokens)
- **AND** the tokens are persisted encrypted
- **AND** `GET /api/claude/accounts` reflects the new account

(Other scenarios inherited unchanged from `add-claude-oauth-link/specs/claude-oauth-pool/spec.md`:
`state_mismatch`, `account_already_exists`, `id_token_missing`,
`id_token_claims_incomplete`, `invalid_grant from Anthropic`,
`New /start supersedes pending flow`, `Flow TTL expiry`, `Flow not found`.)
