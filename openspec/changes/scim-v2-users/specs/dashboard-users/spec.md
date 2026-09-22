## MODIFIED Requirements

### Requirement: Accounts are deactivated through one shared function

Deactivating an account SHALL go through one `deactivate_user()` function that owns the whole effect — status, session generation, the owned-key cascade, the audit row — so every caller behaves the same and none can bypass the break-glass guard. In this release its caller is the SCIM `active=false` endpoint, where a refusal MUST surface as a SCIM `409` and MUST audit `scim_deprovision_refused` exactly once — from inside this function, which already emits it, so the route MUST NOT audit it a second time — and a twin `reactivate_user()` of the same principal-free shape (`actor`, `actor_ip`, `source`, no `DashboardPrincipal`) serves `active: true`. The status change of the user PATCH and the identity resolver's demotion path still reach the effect through the shared repository primitives rather than through this function; an earlier revision of this requirement named them as its callers, which the code has never done, and routing them through it is a separate concern with its own regression surface. Until that lands, the resolver's demotion is the one deactivation that does not cascade the account's owned keys, which is a defect recorded here rather than a behaviour to copy. Because the resolver's path runs on a request-serving code path and would otherwise raise on every cache TTL, a refusal there SHALL pin the account to `role_source=manual` and audit once, exactly as the last-admin rule already does, instead of raising repeatedly. When the function's conditional write matches no row it SHALL re-read the counts and name the invariant that actually refused, as the user PATCH and the delete path already do, rather than always reporting the last-admin one. An account whose status is `invited` SHALL be deactivated rather than silently ignored: its invite is deleted in the same transaction and the row moves to `disabled`, keeping a row that a back-channel caller must still be able to find — unlike a human revoke, which deletes the account. Deleting it SHALL audit the same `invite_revoked` action the management side writes, so one search answers when an invitation stopped working whoever ended it. An account that is already inactive SHALL return without writing or auditing, so a redelivered back-channel push is idempotent.

#### Scenario: One implementation, one behaviour

- **GIVEN** the last qualifying break-glass account while `local_login_policy` is `break_glass_only`
- **WHEN** it is deactivated through the user PATCH and through the shared function directly
- **THEN** both refuse with `409 last_break_glass_protected` and neither cascades any key

#### Scenario: The resolver does not spin on a refusal

- **GIVEN** a provider rule whose no-match role is null, so it would disable a break-glass account
- **WHEN** the account signs in twice across the identity-cache window
- **THEN** the account stays active, its `role_source` is `manual`, and exactly one refusal is audited

#### Scenario: A refusal names the invariant that actually lost

- **GIVEN** a deactivation whose conditional write matches no row because another writer removed the last other qualifying break-glass account first
- **WHEN** the function re-reads the counts
- **THEN** the refusal is `last_break_glass_protected` rather than `last_admin_protected`, and the reverse race reports the reverse

#### Scenario: An invited account is deactivated rather than ignored

- **GIVEN** an `invited` account with a live `sso_only` invite, which never expires
- **WHEN** the shared function deactivates it
- **THEN** the invite row is deleted, the account row remains with status `disabled`, and both the deactivation and the invitation's end are audited
- **AND** a second deactivation of the same account writes and audits nothing

#### Scenario: The refusal is audited once, by the function

- **GIVEN** a back-channel caller that maps the refusal to its own error envelope
- **WHEN** the refusal is raised
- **THEN** exactly one `scim_deprovision_refused` row exists for it, written by the shared function, and the caller adds none
