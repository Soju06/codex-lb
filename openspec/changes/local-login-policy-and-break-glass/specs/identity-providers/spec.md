## MODIFIED Requirements

### Requirement: Provider settings API

`GET /api/auth-providers` (`security:write`) SHALL list every row as `{id, kind, providerKey, label, enabled, active, unknownIdentityRoleId, noMatchRoleId, linkByEmail, skipRoleSync, idpMfaEnforced, config, createdAt, updatedAt}` where `config` masks every secret (`****` + last four; empty in this release) and `active` is computed. `PATCH /api/auth-providers/{id}` (`security:write` and an attributable account, else `409 admin_account_required`) SHALL accept `label`, `enabled`, `unknownIdentityRoleId`, `noMatchRoleId` (null allowed), `linkByEmail`, `skipRoleSync`, `idpMfaEnforced`; unknown fields answer `422`; an unknown id `404 provider_not_found`; a role that is not assignable `422 role_not_assignable`; a role whose grants exceed the caller's `403 insufficient_delegation`. Enabling a provider whose kind offers no local password fallback SHALL require at least one qualifying break-glass account and SHALL otherwise answer `409 break_glass_requires_totp` whose body names the designated account that would qualify — the same refusal, for the same reason, as tightening `local_login_policy`: an install must keep one way in that does not depend on the identity provider. Disabling a provider SHALL never be gated, so the recovery direction is always open. Like the policy tightening it mirrors, the count and the provider write SHALL be one atomic step: a request that could enable a provider SHALL take the account write intent before its first read and hold it until the commit, so a concurrent account mutation cannot remove the last qualifying account in between. A change SHALL audit `provider_updated` (target `auth_provider`) with the changed fields, and an `enabled` change SHALL additionally audit `provider_enabled` or `provider_disabled`; every change SHALL invalidate the registry on every replica.

#### Scenario: Operator changes the default role

- **WHEN** an admin patches the trusted-header row's `unknownIdentityRoleId` to the viewer preset
- **THEN** the next unknown identity becomes a viewer, earlier accounts keep their roles, and `provider_updated` names the caller

#### Scenario: Role handout is bounded by the caller's grants

- **GIVEN** a caller whose role holds `security:write` but not the admin grants
- **WHEN** it sets `unknownIdentityRoleId` to the admin preset
- **THEN** the response is `403 insufficient_delegation`, while the viewer preset succeeds

#### Scenario: Enabling a password-less sign-in method without an emergency account

- **GIVEN** the designated `admin` account has no TOTP secret
- **WHEN** an admin enables a provider whose kind offers no local password fallback
- **THEN** the response is `409 break_glass_requires_totp`, its body names `admin`, and the row stays disabled

#### Scenario: Enabling succeeds once the account qualifies

- **WHEN** that account enrols a second factor and the change is repeated
- **THEN** the response is `200`, and `provider_updated` and `provider_enabled` are both audited

#### Scenario: Disabling is never gated

- **GIVEN** no account qualifies
- **WHEN** an admin disables that provider
- **THEN** the response is `200` and `provider_disabled` is audited
