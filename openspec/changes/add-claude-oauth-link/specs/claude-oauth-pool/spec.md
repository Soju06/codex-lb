# claude-oauth-pool Specification (delta)

This delta extends the `claude-oauth-pool` capability (introduced by change `add-claude-oauth-pool`) with the OAuth-based add flow: authorization code + PKCE + copy-paste code entry. The existing "Manual Claude account add" requirement is preserved unchanged.

## ADDED Requirements

### Requirement: Claude account add via OAuth

The system SHALL expose `POST /api/claude/oauth/start`, `GET /api/claude/oauth/status`, and `POST /api/claude/oauth/callback` to add a Claude account through an authorization-code flow with PKCE and copy-paste code entry. The system SHALL:

- Generate an authorization URL with `response_type=code`, `code_challenge` (method `S256`), and a server-generated `state` token.
- Return the `state` token in the `/start` response payload (under a `stateToken` field) so the dashboard dialog can submit it unchanged on the `/callback` request. The token is exposed only to authenticated dashboard sessions — the dashboard session IS the trust boundary. The `/status` endpoint continues to NOT echo the state token.
- Require the user to paste the authorization `code` and the matching `state` back into `POST /api/claude/oauth/callback` to complete the flow.
- Exchange the code at the Anthropic OAuth token endpoint using the stored PKCE `code_verifier`, the configured `redirect_uri`, and the configured `client_id`.
- Parse the `id_token` from the token response. If `id_token` is missing or does not contain `claude_account_uuid`, the system SHALL reject the request with HTTP 400 and `error_code` `id_token_missing` or `id_token_claims_incomplete`, and SHALL direct the operator to use the manual paste endpoint.
- Persist the new account via the same encryption + insert path used by the existing `POST /api/claude/accounts` (manual paste) endpoint. The fields `claude_account_uuid`, `scopes`, `user_email`, and `user_organization_uuid` SHALL be sourced from the parsed `id_token` claims; `access_token`, `refresh_token`, and `expires_in` SHALL be sourced from the token response body.
- Reject the callback when the supplied `state` does not match the stored state token (HTTP 400, `error_code: state_mismatch`).
- Reject the callback when the `code` is empty (HTTP 400, `error_code: missing_code`).
- Reject a duplicate `claude_account_uuid` with HTTP 409 (`error_code: account_already_exists`).
- Support at most one in-flight flow at a time. A new `POST /api/claude/oauth/start` SHALL transition any prior `pending` flow to `error` with `error_code: superseded`, and SHALL create a new flow with a fresh state token and PKCE pair.
- Consider a pending flow expired when `started_at + claude_oauth_flow_ttl_seconds < now()` at the moment of a status or callback request (lazy evaluation; no background sweeper task is required). Past that point the callback SHALL return HTTP 410 (`error_code: flow_expired`) and the status endpoint SHALL report `status: error` with `error_code: flow_expired`.
- Surface Anthropic token-exchange errors (HTTP 400 `invalid_grant`, 5xx) as HTTP 502 with the original error code preserved (`invalid_grant`, `anthropic_unreachable`).
- Never log `access_token`, `refresh_token`, `id_token`, `code`, `code_verifier`, or `state` in plaintext.

#### Scenario: Happy path

- **WHEN** admin completes the OAuth flow and pastes the `code` and the matching `state`
- **THEN** the system returns 200 with the new account's public payload (no plaintext tokens)
- **AND** the tokens are persisted encrypted
- **AND** `GET /api/claude/accounts` reflects the new account

#### Scenario: state_mismatch

- **WHEN** admin pastes a `state` that does not match the stored token for the flow
- **THEN** the system returns 400 with `error_code: state_mismatch`
- **AND** the flow state is unchanged (still `pending`)

#### Scenario: account_already_exists

- **GIVEN** a Claude account with the same `claude_account_uuid` already exists in the pool
- **WHEN** admin completes the OAuth flow for that account
- **THEN** the system returns 409 with `error_code: account_already_exists`
- **AND** the flow is marked `error`

#### Scenario: id_token_missing

- **WHEN** the Anthropic token response does not include `id_token`
- **THEN** the system returns 400 with `error_code: id_token_missing`
- **AND** the response body instructs the operator to use the manual paste endpoint

#### Scenario: id_token_claims_incomplete

- **WHEN** the Anthropic token response includes `id_token` but the parsed claims do not contain a usable `claude_account_uuid`
- **THEN** the system returns 400 with `error_code: id_token_claims_incomplete`
- **AND** the response body instructs the operator to use the manual paste endpoint

#### Scenario: invalid_grant from Anthropic

- **WHEN** Anthropic's token endpoint returns 400 with `error: invalid_grant`
- **THEN** the system returns 502 with `error_code: invalid_grant`
- **AND** the flow is marked `error`

#### Scenario: New /start supersedes pending flow

- **GIVEN** a flow is in status `pending`
- **WHEN** admin calls `POST /api/claude/oauth/start` again
- **THEN** the previous flow transitions to `error` with `error_code: superseded`
- **AND** a new flow is created with a fresh state token and PKCE pair

#### Scenario: Flow TTL expiry

- **GIVEN** a flow has been `pending` longer than `claude_oauth_flow_ttl_seconds`
- **WHEN** admin posts the callback
- **THEN** the system returns 410 with `error_code: flow_expired`
- **AND** the flow is marked `error`

#### Scenario: Flow not found

- **WHEN** admin posts a callback with a `flow_id` that does not exist (or that was superseded)
- **THEN** the system returns 404 with `error_code: flow_not_found`

#### Scenario: Flow not pending

- **GIVEN** a flow is in status `success` or `error`
- **WHEN** admin posts a callback for that flow
- **THEN** the system returns 409 with `error_code: flow_not_pending`

#### Scenario: Status lookup for unknown flow

- **WHEN** admin calls `GET /api/claude/oauth/status?flowId={id}` with a `flow_id` that does not exist
- **THEN** the system returns 404 with `error_code: flow_not_found`

#### Scenario: Empty code or state

- **WHEN** admin posts a callback with an empty `code` or empty `state`
- **THEN** the system returns 400 (Pydantic validation rejects before the service is invoked)

#### Scenario: No plaintext tokens in logs

- **WHEN** any OAuth flow endpoint is exercised end to end (start, status, callback)
- **THEN** no log line SHALL contain the values of `code`, `state`, `code_verifier`, `access_token`, `refresh_token`, or `id_token`

### Requirement: OAuth flow state machine is single-in-flight

The system SHALL keep at most one non-terminal Claude OAuth flow at a time. The state machine SHALL be in-memory (per process) and SHALL expose three states: `pending`, `success`, `error`. State transitions:

- `idle → pending` on `POST /api/claude/oauth/start`.
- `pending → success` on a successful `POST /api/claude/oauth/callback`. The response body SHALL include the new `account_id`.
- `pending → error` on a failed `POST /api/claude/oauth/callback`, on TTL expiry, or on being superseded by a new `POST /api/claude/oauth/start`.

#### Scenario: Successful transition

- **WHEN** the callback succeeds
- **THEN** the flow transitions to `success`
- **AND** `GET /api/claude/oauth/status?flowId=...` returns `status: "success"` and `account_id` populated

#### Scenario: Single-flight

- **GIVEN** a `pending` flow exists
- **WHEN** admin calls `POST /api/claude/oauth/start` again
- **THEN** only the new flow is `pending`
- **AND** the prior flow is `error: superseded`

### Requirement: OAuth callback validates CSRF state token

The system SHALL generate a cryptographically random `state` token at `/start` and store it alongside the flow. The `POST /api/claude/oauth/callback` SHALL compare the supplied `state` with the stored token; a mismatch SHALL be rejected with HTTP 400 and `error_code: state_mismatch`.

#### Scenario: Valid state

- **WHEN** admin pastes the same `state` that the URL contained
- **THEN** the system proceeds with the token exchange

#### Scenario: Mismatched state

- **WHEN** admin pastes a `state` that differs from the stored token
- **THEN** the system returns 400 with `error_code: state_mismatch`

### Requirement: id_token claims populate Claude account fields

The system SHALL decode the `id_token` returned by the Anthropic token endpoint (JSON decode only; signature verification is out of scope) and SHALL source the following Claude account fields from the claim set:

- `claude_account_uuid` — first match among `account_id`, `sub` (if UUID-shaped), `https://api.anthropic.com/account_id`.
- `user_email` — first match among `email`, `https://api.anthropic.com/email`.
- `user_organization_uuid` — first match among `organization_id`, `org_id`, `https://api.anthropic.com/organization_id`.
- `scopes` — split of `scope` or `scp` claim by whitespace.

If the required field `claude_account_uuid` cannot be resolved, the system SHALL return HTTP 400 with `error_code: id_token_claims_incomplete`.

#### Scenario: Standard Anthropic claims

- **WHEN** the `id_token` payload contains `account_id`, `email`, and `organization_id` claims
- **THEN** the new account row stores those values verbatim
- **AND** `scopes` is parsed from the `scope` claim

#### Scenario: Alternative claim names

- **WHEN** the `id_token` payload uses the namespaced variant `https://api.anthropic.com/account_id` instead of `account_id`
- **THEN** the new account row still stores a non-null `claude_account_uuid`
