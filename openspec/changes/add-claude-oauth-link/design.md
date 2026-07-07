# Design — Claude account add via OAuth (link)

> Source of truth: this document. The delta spec at `specs/claude-oauth-pool/spec.md` is normative.
> The companion change `add-claude-oauth-pool` introduced the manual paste path; this change
> adds the OAuth path that the original proposal deferred.

## Goal

Replace the requirement to manually extract Anthropic OAuth tokens from a Claude Code session
and paste them into `POST /api/claude/accounts` with a one-click link-based flow that matches
the way `claude code` itself does authorization:

1. Operator clicks **Add Claude account via OAuth** in the dashboard.
2. `codex-lb` generates an authorization URL with PKCE and a CSRF state token.
3. Operator copies the URL, opens it in their browser, completes the consent on `claude.ai`.
4. `claude.ai` renders the OOB-style code page at `https://console.anthropic.com/oauth/code`.
5. Operator copies the code from that page, pastes it (plus the state — usually auto-filled
   in the dialog) back into the dashboard form and submits.
6. `codex-lb` performs the authorization-code exchange with PKCE, parses the `id_token`,
   and persists the new account via the same encryption + insert path used by manual paste.

No local HTTP callback server is required and no port (1455 or otherwise) needs to be
reachable from the browser. This makes the flow work identically on localhost, behind a
reverse proxy, in Docker, or on a headless server.

## Non-goals

- Live browser-clickable OAuth (server-side browser open) — out of scope; user-driven.
- JWKS-based signature verification of `id_token` — out of scope (matches codex-flow).
- Cross-replica state synchronization for the flow state machine — out of scope (documented).
- Device-code flow for Claude — out of scope (no known Anthropic device endpoint as of design time).
- Removal of the existing `POST /api/claude/accounts` (manual paste) — it stays for fallback.

## Architecture

```
app/modules/claude/oauth/   ← new module, isolated from app/modules/oauth/
├── __init__.py
├── api.py        FastAPI router: /api/claude/oauth/{start,callback,status}
├── service.py    ClaudeOAuthService — state machine, CSRF, TTL, single-flight
├── schemas.py    Pydantic models (DashboardModel base, camelCase aliases)
└── tokens.py     PKCE pair generation + id_token JWT decode + claim mapping
```

Other touchpoints:

- `app/modules/claude/auth_manager.py` — add `add_claude_account_from_oauth(...)` (thin
  wrapper around the existing `add_claude_account(...)`).
- `app/core/clients/anthropic/oauth.py` — add `ClaudeOAuthClient.exchange_authorization_code(...)`
  (sibling of the existing `refresh(...)`).
- `app/core/config/settings.py` — five new settings keys (see §Settings).
- `app/main.py` — wire the new router under `/api/claude/oauth`.
- `frontend/src/components/claude/AddClaudeAccountOAuthDialog.tsx` — new dialog component.
- `frontend/src/components/claude/ClaudeAccountList.tsx` — add "Add via OAuth" button.

Untouched:

- `app/modules/oauth/*` (Codex OAuth flow) — no edits.
- `app/modules/proxy/service.py` — no edits (ADR-0001 preserved).
- `app/modules/claude/api.py` (admin CRUD) — no edits; the new sub-router is added alongside.
- `POST /api/claude/accounts` (manual paste) — no edits; regression-guard test added.

## Data flow (happy path)

```
┌─────────────────────────────────────────────────────────────────────┐
│ Dashboard (React)              codex-lb (FastAPI)      Anthropic     │
│                                                                     │
│ 1. click "Add via OAuth" ───► POST /api/claude/oauth/start          │
│                                 │                                  │
│                                 │ generate state, PKCE pair        │
│                                 │ supersede any prior pending flow │
│                                 │                                  │
│ ◄── 200 {flow_id, authorization_url,                                │
│          expires_in_seconds, callback_instructions,                 │
│          redirect_uri}                                              │
│                                                                     │
│ 2. dashboard shows URL with "Copy" + "Open in new tab" buttons       │
│    and a textarea for the code.                                     │
│                                                                     │
│ 3. user opens URL in browser ────────────────────────────────►     │
│    claude.ai renders consent, then                                  │
│    redirects to https://console.anthropic.com/oauth/code?code=...   │
│    (the page shows "AUTH_CODE_..." to copy)                         │
│                                                                     │
│ 4. user pastes code + state ─► POST /api/claude/oauth/callback      │
│                                 {flow_id, code, state}              │
│                                                                     │
│                                 │ lookup flow by id                 │
│                                 │ verify state == flow.state_token  │
│                                 │ check flow.status == "pending"   │
│                                 │                                  │
│                                 │ POST {claude_oauth_token_endpoint}│
│                                 │   JSON:                           │
│                                 │     grant_type=authorization_code │
│                                 │     code=...                       │
│                                 │     code_verifier=<stored PKCE>   │
│                                 │     client_id                      │
│                                 │     redirect_uri                   │
│                                 │                                  │
│                                 │ ◄── 200 {access_token,            │
│                                 │          refresh_token,            │
│                                 │          id_token,                 │
│                                 │          expires_in, scope}        │
│                                                                     │
│                                 │ decode id_token claims            │
│                                 │ map to ClaudeOauthClaims          │
│                                 │                                  │
│                                 │ ClaudeAuthManager                 │
│                                 │   .add_claude_account_from_oauth( │
│                                 │     tokens, claims)               │
│                                 │ → encrypt + INSERT accounts row    │
│                                                                     │
│ ◄── 200 {status: "success",                                          │
│          account: ClaudeAccountResponse}                            │
│                                                                     │
│ 5. dashboard refreshes account list, shows new row.                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Component responsibility table

| Layer                    | Responsibility                                                  |
|--------------------------|-----------------------------------------------------------------|
| `api.py`                 | HTTP envelope, dashboard session + write-access gate, error → HTTP mapping |
| `service.py`             | State machine (idle/pending/success/error), CSRF (state token), TTL, single-in-flight, Anthropic call orchestration |
| `tokens.py`              | PKCE pair generation (S256), `id_token` JWT decode, claim mapping fallback chain |
| `auth_manager.add_claude_account_from_oauth` | Reuse the existing encryption + insert path; do NOT re-validate claim fields |
| `core/clients/anthropic/oauth.py::exchange_authorization_code` | HTTP transport to token endpoint, response parsing, error model parity with `refresh` |

## State machine

```
       ┌────────┐  start (or replace)        ┌─────────┐
       │  idle  │ ──────────────────────────►│ pending │
       └────────┘                            └────┬────┘
                                                  │
                                  callback OK     │   callback fail /
                                                  │   flow expired /
                                                  ▼   superseded
                                            ┌─────────┐
                                            │ success │  terminal
                                            └─────────┘
                                                  │
                                                  │  callback fail /
                                                  │  TTL expiry /
                                                  │  superseded
                                                  ▼
                                            ┌─────────┐
                                            │  error  │  terminal
                                            └─────────┘
```

Key invariants:

- One non-terminal flow at a time (per process). New `/start` transitions any existing
  pending flow to `error: superseded`.
- Pending flows are considered expired lazily on access. A pending flow is expired when
  `started_at + claude_oauth_flow_ttl_seconds < now()` at the moment a status or
  callback request is processed. No background sweeper task is required; an idle flow
  past its TTL stays in memory as `pending` until the next access.
- Terminal states are retained for at least one status poll so the dashboard can display
  the final result after a page refresh.
- Multi-replica caveat: the state store is process-local. If `/start` lands on replica A
  and `/callback` lands on replica B, the callback receives 404 `flow_not_found`. This
  matches the behavior of the existing `app/modules/oauth/` flow; a shared-state follow-up
  is explicitly out of scope.

## API contract

| Method | Path                              | Body                       | Response                          | Errors |
|--------|-----------------------------------|----------------------------|-----------------------------------|--------|
| POST   | `/api/claude/oauth/start`         | `{}`                       | `{flow_id, authorization_url, state_token, expires_in_seconds, callback_instructions, redirect_uri}` | 502 |
| GET    | `/api/claude/oauth/status?flowId=`| (query)                    | `{flow_id, status, error_message?, error_code?, account_id?, started_at, finished_at?}` | — |
| POST   | `/api/claude/oauth/callback`      | `{flow_id, code, state}`   | `{status: "success", account: ClaudeAccountResponse}` | 400 / 404 / 409 / 410 / 502 |

The full `error_code` enumeration:

| `error_code`                | HTTP | Cause |
|-----------------------------|------|-------|
| `flow_not_found`            | 404  | `flow_id` not present or already superseded |
| `flow_expired`              | 410  | TTL elapsed |
| `flow_not_pending`          | 409  | flow is already success/error |
| `state_mismatch`            | 400  | pasted `state` does not match stored token |
| `missing_code`              | 400  | `code` empty |
| `invalid_grant`             | 502  | Anthropic returned 400 `invalid_grant` |
| `anthropic_unreachable`     | 502  | Anthropic 5xx or network failure |
| `id_token_missing`          | 400  | token response omitted `id_token` |
| `id_token_malformed`        | 400  | `id_token` is not a parseable JWT |
| `id_token_claims_incomplete`| 400  | no usable `account_id` claim |
| `account_already_exists`    | 409  | UUID already in pool |
| `superseded`                | —    | internal: set on prior pending flow when a new `/start` is issued |

## Settings

```python
# Existing (already declared, only needs to be wired):
claude_oauth_authorize_endpoint: str = "https://platform.claude.com/oauth/authorize"
claude_oauth_token_endpoint:     str = "https://platform.claude.com/v1/oauth/token"

# New in this change:
claude_oauth_client_id:                       str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
claude_oauth_redirect_uri:                    str = "https://console.anthropic.com/oauth/code"
claude_oauth_scopes:                          str = "user:profile user:inference"
claude_oauth_flow_ttl_seconds:                int = 600
claude_oauth_authorization_code_max_length:   int = 4096
```

Defaults are sourced from `app/core/clients/anthropic/oauth.py::ANTHROPIC_OAUTH_CLIENT_ID`
and the verified redirect URI used by the public Claude Code client. All five env vars are
overridable through `CODEX_LB_CLAUDE_OAUTH_*`.

## Authorization URL

```
{claude_oauth_authorize_endpoint}
    ?response_type=code
    &client_id={claude_oauth_client_id}
    &redirect_uri={quote(claude_oauth_redirect_uri)}
    &scope={quote(claude_oauth_scopes)}
    &state={state_token}
    &code_challenge={code_challenge}
    &code_challenge_method=S256
```

## PKCE

`tokens.generate_pkce_pair() -> (verifier, challenge)`:

- `verifier = secrets.token_urlsafe(64)`  (≈ 86 base64url chars; > RFC 7636 minimum of 43).
- `challenge = base64.urlsafe_b64encode(sha256(verifier)).rstrip(b"=").decode("ascii")`.

Only S256 is supported. The verifier never leaves the server — it is exchanged in the
POST to the token endpoint body and never logged.

## CSRF / state protection

- `state_token = secrets.token_urlsafe(32)` — 256 bits of entropy.
- Stored alongside the flow in the in-memory state store.
- Returned to the dashboard inside the `/start` response payload (under
  `stateToken`) so the dialog can submit it unchanged on the `/callback`. The
  dashboard session IS the trust boundary — the operator already had to
  authenticate to reach the dialog. The `/status` endpoint continues to NOT
  echo the state token to any external caller (the spec delta pins this).
- The pasted `state` is compared with the stored value; mismatch → 400 `state_mismatch`.

## Token exchange

`ClaudeOAuthClient.exchange_authorization_code(*, code, code_verifier, redirect_uri)`
posts JSON to `claude_oauth_token_endpoint` with body:

```json
{
  "grant_type": "authorization_code",
  "code": "<pasted>",
  "code_verifier": "<stored>",
  "client_id": "<from settings>",
  "redirect_uri": "<from settings>"
}
```

Response (200):

```json
{
  "access_token":  "sk-ant-oat01-...",
  "refresh_token": "...",
  "id_token":      "<jwt>",     // tolerated to be absent
  "expires_in":    3600,
  "scope":         "user:profile user:inference",
  "token_type":    "Bearer"
}
```

Error semantics mirror `refresh`:

- 400 with `error == "invalid_grant"` → `ClaudeAuthError`.
- 5xx → `ClaudeUpstreamError`.
- Parse failure → `ClaudeAPIError`.
- Missing `id_token` is tolerated; downstream code returns 400 `id_token_missing`.

## id_token decode + claim mapping

`tokens.decode_id_token(jwt) -> ClaudeOauthClaims` performs a JSON-decode only (no
signature verification; matches the convention in `app/core/auth/models.py::extract_id_token_claims`).
Claims are sourced in priority order:

| Field                          | First-match claim keys                                                       |
|--------------------------------|------------------------------------------------------------------------------|
| `claude_account_uuid` (REQ)    | `account_id`, `sub` (if UUID-shaped), `https://api.anthropic.com/account_id`  |
| `user_email`                   | `email`, `https://api.anthropic.com/email`                                    |
| `user_organization_uuid`       | `organization_id`, `org_id`, `https://api.anthropic.com/organization_id`     |
| `scopes`                       | split `scope` or `scp` by whitespace                                          |

If `claude_account_uuid` cannot be resolved, the service returns 400
`id_token_claims_incomplete`.

## Persistence path

`ClaudeAuthManager.add_claude_account_from_oauth(*, access_token, refresh_token, expires_in, id_token_claims) -> str`
delegates to the existing `add_claude_account(...)` with values derived from the typed
`id_token_claims`. Storage, encryption, and duplicate-UUID behavior are identical to the
manual paste path.

## Frontend changes

### Dialog steps (single dialog component, step indicator)

1. **Idle**: primary "Add Claude account via OAuth" button + secondary "Or paste tokens manually" link (opens the existing `AddClaudeAccountDialog`).
2. **Started** (after `/start`): show URL with **Copy** + **Open in new tab** buttons, followed by
   a textarea for the code, a read-only `state` field (auto-filled from the start response),
   and the **Submit** button. A small help line renders `callback_instructions` from the server.
3. **Success**: reuse the `<ClaudeAccountList>` row layout to preview the new account, then
   close the dialog and refresh the list.
4. **Error**: show `error_code` + `error_message` + retry button. For terminal states
   (`flow_not_found`, `flow_expired`, `superseded`) show "Start over" which issues a new `/start`.

Polling is NOT required (synchronous paste flow). `GET /status` is used only for page-refresh recovery.

### i18n keys (en + zh-CN)

`claude.oauth.add.button`, `claude.oauth.add.manualLink`,
`claude.oauth.step1.title`, `claude.oauth.step1.copy`, `claude.oauth.step1.open`,
`claude.oauth.step2.title`, `claude.oauth.step2.codePlaceholder`, `claude.oauth.step2.stateLabel`,
`claude.oauth.step2.submit`, `claude.oauth.step3.title`, `claude.oauth.error.title`,
`claude.oauth.error.startOver`, and one key per documented `error_code` for the user-facing
mapping (e.g. `claude.oauth.error.flow_expired` → "This authorization request expired…").

### TypeScript

Three new zod schemas in `frontend/src/lib/schemas.ts`:
`ClaudeOauthStartResponseSchema`, `ClaudeOauthCallbackRequestSchema`,
`ClaudeOauthStatusResponseSchema`.

## Structured logging

Log lines MUST NOT contain plaintext tokens, codes, or PKCE material.

- `claude.oauth.flow.started  {flow_id, request_id}`
- `claude.oauth.flow.callback {flow_id, status: "success"|"error", error_code?, account_id?}`
- `claude.oauth.token.exchange {flow_id, anthropic_http_status, duration_ms}`
- `claude.oauth.account.created {flow_id, account_id, claude_account_uuid}`

## Security & threat model

| Threat                                                | Mitigation                                              |
|-------------------------------------------------------|---------------------------------------------------------|
| CSRF via crafted `code` from another user's flow       | `state` token, 256-bit, validated server-side           |
| PKCE downgrade                                        | S256 only; no `plain` method                            |
| Code interception (plaintext in logs)                 | Structured logs never carry `code` / `state` / verifier |
| Token theft via API response                          | `_serialize_account` strips token-denylisted columns (existing invariant) |
| Replay of pasted code                                  | Verifier is single-use; Anthropic returns `invalid_grant` on reuse; flow is marked `error` |
| Brute-force `flow_id`                                 | `secrets.token_urlsafe(12)` ≈ 144 bits entropy; negligible |
| Dashboard CSRF                                         | Existing `validate_dashboard_session` + `require_dashboard_write_access` dependencies |
| Multi-replica cookie stickiness                        | Documented as out-of-scope follow-up; matches codex-flow |

## Tests

### Unit (new files)

| File                                       | Coverage |
|--------------------------------------------|----------|
| `tests/unit/test_claude_oauth_tokens.py`   | PKCE generation; S256 verification; `decode_id_token` happy path, claim-mapping fallback chain, missing `id_token` returns `None`, malformed JWT raises typed error, incomplete claims |
| `tests/unit/test_claude_oauth_service.py`  | State transitions, single-flight supersede, TTL expiry, CSRF state mismatch, full stub-Anthropic happy path, every documented `error_code` |
| `tests/unit/test_claude_oauth_api.py`      | HTTP envelope (status codes, error codes), auth dependency, request validation, response shape |
| `tests/unit/test_anthropic_oauth_client.py` (extend) | `exchange_authorization_code`: happy path, `invalid_grant`, 5xx, missing `id_token` tolerated |
| `tests/unit/test_claude_auth_manager_oauth.py` (extend) | `add_claude_account_from_oauth`: round-trip, `ClaudeAccountAlreadyExists` propagation |

### Integration

| File                                                | Coverage |
|-----------------------------------------------------|----------|
| `tests/integration/test_claude_oauth_flow.py`        | full happy path: start → mock Anthropic → callback → row created → list endpoint shows new account |
| `tests/integration/test_claude_oauth_errors.py`      | `state_mismatch`, `flow_not_found`, `flow_expired` (mock time), `account_already_exists`, `id_token_missing`, `invalid_grant` propagation |
| `tests/integration/test_claude_oauth_manual_paste_unchanged.py` | regression guard: `POST /api/claude/accounts` (manual) still works after the change |

### Out of scope for tests

- Live Anthropic API calls (zero network).
- Browser-side copy/paste UX (Playwright not added; i18n + zod cover UI contract).
- Multi-replica state coherence (acknowledged limitation; covered by integration on a single replica).

## OpenSpec artifact map

- `proposal.md` — why + what.
- `tasks.md` — checklist used during implementation.
- `specs/claude-oauth-pool/spec.md` — `## ADDED Requirements` with `#### Scenario:` blocks (this document is the design rationale; the spec is normative).
- `context.md` — operational notes (multi-replica caveat, redirect URI verification sources, "use manual paste fallback" guidance for operators).
- `design.md` — this file.
- `README.md` — index.

## Risks and trade-offs (honest list)

| Risk | Severity | Mitigation |
|------|----------|------------|
| Anthropic `id_token` claim names differ from the assumed priority chain | M | Best-effort mapping with explicit fallback keys; if no `account_id` is found the flow returns 400 with `id_token_claims_incomplete` and points to manual paste |
| `redirect_uri = https://console.anthropic.com/oauth/code` changes (Anthropic does not publish semver for OAuth endpoints) | L | Configured via `claude_oauth_redirect_uri` env var; bumping requires only a config change |
| Single-in-flight UX (operator cannot add two accounts in parallel) | L | Dialog disables the "Add" button while a flow is pending; `superseded` error message is explicit |
| PKCE verifier lives in process memory for up to 10 minutes; process restart drops the flow | L | Matches codex-flow; "Start over" button on the dialog |
| OpenSpec strict validator: ADDED Requirements must use `#### Scenario:` blocks | M | All scenarios in the delta use the exact format |
| `id_token` signature is not verified | M | Matches project convention; documented in `context.md`; signature verification is a separate, larger change if requested |
| Multi-replica: callback on a different pod returns 404 | M | Documented; follows the same caveat as codex-flow; sticky-session header or shared Redis state is out of scope |

## Out-of-scope follow-ups (suggested, NOT in this change)

- Stick-session routing so `/start` and `/callback` land on the same replica.
- Shared state store (Redis) for cross-replica flow state.
- Device-flow attempt against Anthropic (no public documentation at design time).
- `id_token` JWKS-based signature verification.
