## ADDED Requirements

### Requirement: Sign-in providers are rows with an implementation

The system SHALL keep one row per way of signing in in `dashboard_auth_providers` (`id`, `kind` ∈ {`password`, `trusted_header`, `oidc`}, `provider_key`, `enabled`, `label`, `config_encrypted`, `unknown_identity_role_id` FK → `dashboard_roles` ON DELETE SET NULL, `no_match_role_id` FK SET NULL, `link_by_email` default false, `skip_role_sync` default false, `idp_mfa_enforced` default false, `created_at`, `updated_at`; UNIQUE(`kind`, `provider_key`)). The migration and the test schema MUST seed `password/default` (enabled, no default roles) and `trusted_header/default` (enabled, `unknown_identity_role_id` = the admin preset, `no_match_role_id` = the viewer preset) with insert-ignore semantics so an operator's later edit survives re-runs and upgrades. A provider is *active* when its row is enabled AND `dashboard_auth_mode` admits its kind: `trusted_header` only in `trusted_header` mode, `password` in every mode, a kind without an implementation never. The mode MUST NOT be persisted into the table. Every provider implements `kind`, `provider_key` and `resolve_identity(request) -> ExternalIdentity | None` (`provider`, `provider_key`, `subject`, `email`, `display_name`, `groups`); redirect-style providers add `begin_login`/`complete_login` when they arrive and still end in the shared resolver.

#### Scenario: Fresh and upgraded installs share the seed

- **WHEN** the migration runs on an empty database or on one that already has the table
- **THEN** exactly two rows exist, the trusted-header row hands unknown identities the admin preset and re-running the migration changes nothing

#### Scenario: Active follows the mode

- **GIVEN** both seeded rows are enabled
- **WHEN** `dashboard_auth_mode` is `standard`
- **THEN** only `password` is active; in `trusted_header` mode both are

### Requirement: The trusted-header provider yields a case-folded identity

`TrustedHeaderProvider` SHALL produce an identity only from the singular, trusted-peer header the existing request-auth resolution accepts, with `subject` = the trimmed header value case-folded, `display_name` = the trimmed raw value, `email` = the value when it parses as an e-mail address (else null), and `groups` empty in this release.

#### Scenario: Two spellings, one identity

- **WHEN** the proxy sends `Alice@Example.com` and later `alice@example.com`
- **THEN** both resolve to the identity `(trusted_header, default, alice@example.com)` with e-mail `alice@example.com`

### Requirement: Identity resolution is one ordered path

Every provider's identity SHALL be mapped to an account by the same resolver, in this order: (1) an existing `dashboard_identities` row for `(provider, provider_key, subject)` → its account (a `disabled` account is refused with `account_disabled`; an `invited` one with `identity_not_provisioned`); (1.5) an `invited` account whose live invite (an SSO-only invite is live until consumed or revoked; the oldest match wins) carries exactly this triple in `expected_provider/expected_provider_key/expected_subject` → the identity row is created, the account becomes `active` (its `session_generation` is bumped), the invite is consumed with a compare-and-set on its liveness, and `identity_linked` is audited with `via: invite`; (2) when the provider's `link_by_email` is true and the identity carries an e-mail that equals an active account's normalised e-mail → the identity row is created and `identity_linked` (`via: email`) audited; (3) otherwise just-in-time provisioning: with `unknown_identity_role_id` NULL (or pointing at a missing role) the identity is refused with `identity_not_provisioned` and `login_failed` is audited at warning severity with `reason: unknown_identity`, `method`, `provider`, `provider_key`, `subject`, `email` and `groups`; with a role the account is created `active`, `role_source=mapping`, `display_name` = the identity's display name, `email` = the identity's e-mail unless another account already holds it, `last_login_at` set, together with its identity row, auditing `user_created` (actor: no account, `auth_method` = the provider kind, details `jit: true` and the identity) and `identity_linked` (`via: jit`). Accounts MUST never be resolved by username. A concurrent first request for the same identity MUST end with one account (the loser re-reads the winner's identity row).

#### Scenario: Unknown identity becomes an admin by default

- **GIVEN** the seeded trusted-header row
- **WHEN** `alice@example.com` makes her first request
- **THEN** an active account `alice.example.com` with the admin preset and `role_source=mapping` exists with one identity row, and `user_created` plus `identity_linked` are audited

#### Scenario: Refused identity

- **GIVEN** `unknown_identity_role_id` is NULL
- **WHEN** an unknown identity makes a request
- **THEN** no account is created, the request is refused with `identity_not_provisioned` and one `login_failed` row names the subject, e-mail and groups

#### Scenario: Pre-created account is linked on first sign-in

- **GIVEN** an invited account whose invite expects `(trusted_header, default, bob@example.com)`
- **WHEN** the proxy first sends `Bob@Example.com`
- **THEN** the account is active with that identity, the invite is consumed, and `identity_linked` records `via: invite`

#### Scenario: E-mail linking is opt-in

- **GIVEN** an active account with e-mail `carol@example.com` and `link_by_email` false
- **WHEN** the proxy sends `carol@example.com`
- **THEN** a separate account `carol.example.com` without an e-mail is created
- **AND** with `link_by_email` true the same request links the identity to the existing account instead

### Requirement: Just-in-time usernames

The JIT username SHALL be `slugify(subject)`: case-folded, `@` replaced by `.`, every character outside `[a-z0-9._-]` removed, cut to 56 characters; an empty result becomes `th-<first 8 hex of sha256(subject)>`. On a UNIQUE collision the resolver SHALL append `-2`, `-3`, … The username `admin` SHALL be treated as taken even before that account exists, so a proxy user named `admin` becomes `admin-2` and can never inherit the migrated break-glass account.

#### Scenario: Slug rules

- **WHEN** subjects `Alice.Smith`, `alice@example.com`, `al ice!`, `!!!` and `admin` are provisioned on an empty install
- **THEN** their usernames are `alice.smith`, `alice.example.com`, `alice`, `th-` + 8 hex characters, and `admin-2`

### Requirement: Existing accounts are not re-evaluated without mappings

With no role mappings on a provider, the resolver MUST NOT change the role, `role_source` or status of an existing account; it MAY refresh the identity row's `email`/`display_name` (when changed), `last_seen_at`, and the account's `last_login_at`. Changing `unknown_identity_role_id` MUST affect only accounts provisioned afterwards.

#### Scenario: Upgraded reverse-proxy install keeps its admins (D10)

- **GIVEN** accounts with trusted-header identities holding the admin and viewer presets with `role_source=manual`
- **WHEN** each identity makes a request after the upgrade
- **THEN** both accounts keep their role and `role_source`, no account is created, and the viewer's session carries no `write` alias

### Requirement: Resolution is throttled per identity

The resolution result SHALL be cached per `(provider, provider_key, subject)` for the users-cache TTL (5 s): within the TTL a repeated request performs no resolver database work and no audit write. A cached success names only the account id; the request path MUST re-read the account through the users cache so a disable or role change takes effect within the TTL.

#### Scenario: One resolver run per TTL

- **WHEN** the same identity makes four requests within the TTL
- **THEN** the resolver runs once and every request is served

### Requirement: Provider settings API

`GET /api/auth-providers` (`security:write`) SHALL list every row as `{id, kind, providerKey, label, enabled, active, unknownIdentityRoleId, noMatchRoleId, linkByEmail, skipRoleSync, idpMfaEnforced, config, createdAt, updatedAt}` where `config` masks every secret (`****` + last four; empty in this release) and `active` is computed. `PATCH /api/auth-providers/{id}` (`security:write` and an attributable account, else `409 admin_account_required`) SHALL accept `label`, `unknownIdentityRoleId`, `noMatchRoleId` (null allowed), `linkByEmail`, `skipRoleSync`, `idpMfaEnforced`; unknown fields (including `enabled`) answer `422`; an unknown id `404 provider_not_found`; a role that is not assignable `422 role_not_assignable`; a role whose grants exceed the caller's `403 insufficient_delegation`. A change SHALL audit `provider_updated` (target `auth_provider`) with the changed fields and invalidate the registry on every replica.

#### Scenario: Operator changes the default role

- **WHEN** an admin patches the trusted-header row's `unknownIdentityRoleId` to the viewer preset
- **THEN** the next unknown identity becomes a viewer, earlier accounts keep their roles, and `provider_updated` names the caller

#### Scenario: Role handout is bounded by the caller's grants

- **GIVEN** a caller whose role holds `security:write` but not the admin grants
- **WHEN** it sets `unknownIdentityRoleId` to the admin preset
- **THEN** the response is `403 insufficient_delegation`, while the viewer preset succeeds
