## ADDED Requirements

### Requirement: Ordinary refresh preflight may retain an unexpired access token

When an ordinary non-forced freshness preflight for an active account receives
a permanent refresh-credential-only error, it MUST re-read the persisted account
before recovering. It MAY continue the same request using that row's stored
access token only when the row is `reauth_required` and the access token has a
known expiry strictly in the future. The persisted warning MUST match the
refresh error's canonical reason; a synthesized terminal-status error MUST NOT
mask a different session-invalidated warning. The supported refresh-only errors MUST be
`refresh_token_expired`, `refresh_token_reused`, `refresh_token_invalidated`,
`invalid_refresh_token`, and `invalid_grant`.

Recovery MUST NOT clear the refresh warning, rewrite credentials or
`last_refresh`, make an additional upstream probe, or change account ownership.
The upstream resource request MUST still authenticate the token normally.
Forced refresh callers MUST retain the failed refresh result, including when
they share an exchange with a recovering ordinary caller. Transient failures,
unknown or elapsed expiry, missing accounts, paused/deactivated state, and
session/account invalidation MUST NOT qualify for this recovery.

#### Scenario: First request uses the remaining access-token lifetime

- **GIVEN** an active account with stale refresh metadata and an unexpired access token
- **WHEN** ordinary Responses preflight receives `refresh_token_invalidated`
- **THEN** the guarded refresh failure persists `reauth_required`
- **AND** that same request may reach upstream with the stored access token
- **AND** a successful upstream response is returned without changing credentials

#### Scenario: Forced and ordinary callers share only the refresh result

- **GIVEN** ordinary and forced callers sharing one failing refresh exchange
- **WHEN** the ordinary caller can retain an unexpired stored access token
- **THEN** only the ordinary caller recovers
- **AND** the forced caller still receives the permanent refresh failure

#### Scenario: No usable stored access token

- **WHEN** the fresh row is absent, not `reauth_required`, or has unknown or elapsed access-token expiry
- **THEN** the ordinary preflight propagates its original refresh failure

#### Scenario: Authentication rejection remains authoritative

- **WHEN** the resource endpoint rejects the retained access token
- **THEN** forced refresh recovery MUST NOT return that token as a successful refresh
- **AND** the existing authentication failure or safe failover path applies

#### Scenario: Non-refresh-credential failures do not qualify

- **WHEN** refresh fails transiently or reports account/session invalidation
- **THEN** the preflight propagates the failure without access-token recovery
