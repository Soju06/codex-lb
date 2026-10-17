## Why

The built-in `guest` role exists so a team can share a monitoring-only view of the load balancer. Today that view still exposes surfaces a viewer has no business reading: the full API-key inventory (policies, assignments, limits, live usage), the upstream egress-proxy topology and proxy usernames, sticky-session bindings that carry account e-mails, OAuth flow state, the server's connect address, and — on every account list and the dashboard overview — upstream account e-mails and ChatGPT account identifiers. Separately, guest sessions are stateless cookies that cannot be revoked: changing or removing the guest password, or turning guest access off, leaves every already-issued guest cookie valid until it expires.

## What Changes

- Gate inventory and topology reads by the permission they belong to, so guests (who hold only `dashboard:read` and `accounts:read`) receive `403 permission_required`: `GET /api/api-keys*` → `api_keys:read`; `GET /api/settings/upstream-proxy`, `GET /api/settings/runtime/connect-address`, `GET /api/sticky-sessions` → `ops:write`; `GET /api/oauth/status` → `accounts:write`.
- Redact upstream account identity for principals without `accounts:write`: `email` is masked (`a***@example.com`), `chatgptAccountId`, `workspaceId`, and `workspaceLabel` are null, in `GET /api/accounts` and the account list embedded in `GET /api/dashboard/overview`. Request-log free-text search no longer matches account e-mails for such principals; without `api_keys:read`, `GET /api/request-logs/options` returns an empty `apiKeys` list, request-log rows carry null `apiKeyId`/`apiKeyName`, and search does not match key ids or names.
- Add `dashboard_settings.guest_session_generation` (integer, default 0, one Alembic revision). Guest session cookies carry the generation they were issued under and validate only while it matches. The counter is bumped when the guest password is set or removed, when guest access is switched off, and by a new `POST /api/dashboard-auth/guest/logout-all` (`security:write`, audited as `guest_sessions_revoked`). Guest cookies issued before this change carry no generation and stop validating on upgrade (guests simply re-enter).
- Extend the route authorization matrix test with the newly gated routes.

Built-in `admin` behaviour is unchanged. No new setting, env var, README section, or nav item. The dashboard client is adapted in the follow-up change `guest-ui-hides-restricted-surfaces`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: Guest-restricted read surfaces, account identity redaction, and generation-bound guest sessions.

## Impact

- Routes: `api_keys`, `settings`, `sticky_sessions`, `oauth`, `accounts`, `dashboard`, `request_logs`, `dashboard_auth` API modules.
- Mapping: `build_account_summaries(redact_identity=...)`; request-log repository search filter and count cache key gain an `include_account_identity` flag.
- Schema: one new NOT NULL integer column on `dashboard_settings` (`20260908_000000_add_guest_session_generation`), idempotent upgrade/downgrade.
- Session store payload gains `gg` for guest cookies; `DashboardSessionState.guest_session_generation`.
- Tests: guest revocation flows, redaction, search oracle, options inventory, gated reads, migration up/down, matrix expectations.
- Until `guest-ui-hides-restricted-surfaces` lands, a guest opening the APIs page sees the standard "failed to load" card with a retry button, and the Settings page shows an inline error for the upstream-proxy query; no crash, no data.
