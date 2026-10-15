## ADDED Requirements

### Requirement: Group-to-role mappings are rows with a server-owned order

The system SHALL keep one row per rule in `dashboard_role_mappings` (`id`, `provider`, `provider_key`, `claim_name` ∈ {`groups`, `email_domain`} (a plain string column validated in application code so a later claim never needs a type migration), `claim_value`, `role_id` FK → `dashboard_roles` ON DELETE RESTRICT, `priority` NOT NULL, `created_at`, `updated_at`; `UNIQUE(provider, provider_key, priority)`). The Alembic revision SHALL create the table with the unique constraint declared inline (SQLite cannot add one afterwards), guard itself with an inspector so a re-run converges, use batch mode for any ALTER, and mirror the drop in `downgrade`. The table SHALL be seeded empty: an upgrade introduces no rule and therefore no behaviour change.

Priorities SHALL be owned by the server, never by the client: after every write the rows of one `(provider, provider_key)` are the contiguous integers `N…1` with `N` the highest, a create appends at `1` and shifts the others up, a delete closes the gap, and a reorder writes the given order. Each renumber SHALL run in one transaction that first parks the affected rows on a disjoint offset and then writes their final values, so `UNIQUE(provider, provider_key, priority)` never trips mid-update.

#### Scenario: Migration adds an empty table

- **WHEN** the migration runs on an empty database, on a database that already has the table, and on SQLite
- **THEN** `dashboard_role_mappings` exists with `UNIQUE(provider, provider_key, priority)`, holds no rows, and a second run changes nothing

#### Scenario: Reordering never collides

- **GIVEN** four rules with priorities `4, 3, 2, 1`
- **WHEN** the last one is moved to the top
- **THEN** the four rows end as `4, 3, 2, 1` in the new order, the write is one transaction, and no intermediate state violates the unique constraint

#### Scenario: A role in use cannot be dropped silently

- **GIVEN** a rule pointing at a custom role
- **WHEN** that role row is deleted directly
- **THEN** the database refuses the delete (`ON DELETE RESTRICT`), so no rule can point at a missing role

### Requirement: The highest-priority matching rule wins

For an identity the resolver SHALL evaluate the rules of its `(provider, provider_key)` in descending `priority` and take the FIRST match as the single winner; there is no "highest role" rule (custom roles have no total order) and no tie is possible (`UNIQUE(provider, provider_key, priority)`). A `groups` rule matches when the identity's group set contains `claim_value`; an `email_domain` rule matches when the identity's e-mail has that domain. Both comparisons SHALL be made on case-folded, trimmed values, and an `email_domain` value SHALL be stored without a leading `@`. An identity with no e-mail never matches an `email_domain` rule; an identity with no groups never matches a `groups` rule.

#### Scenario: Two rules match, one wins

- **GIVEN** `groups=platform → operator` at priority 2 and `groups=staff → viewer` at priority 1
- **WHEN** an identity arrives in both groups
- **THEN** it gets the operator role, and the viewer rule is not applied

#### Scenario: Case and spelling do not matter

- **GIVEN** a rule `groups=Platform`
- **WHEN** the proxy sends `platform, staff`
- **THEN** the rule matches

#### Scenario: Domain rule as a catch-all

- **GIVEN** `groups=platform → operator` at priority 2 and `email_domain=example.com → member` at priority 1
- **WHEN** `carol@example.com` arrives with no groups
- **THEN** she gets the member role

### Requirement: Existing accounts are re-evaluated only under these rules

When a provider has at least one rule, the resolver SHALL re-evaluate an existing account on the same run that the per-identity throttle already allows, and SHALL apply exactly these rules:

1. `role_source=manual` — the account is NEVER re-evaluated (a person decided this; every break-glass account is `manual`).
2. The provider's `skip_role_sync=true` — no account of that provider is re-evaluated, while just-in-time provisioning still consults the rules.
3. `role_source=mapping` with a matching rule — the account's role becomes the winner's role; an UPDATE is issued only when the role actually differs, and then `user_role_changed` is audited with `source: mapping` and the account's `session_generation` is bumped.
4. `role_source=mapping` with no matching rule — the account moves to the provider's `no_match_role_id`; when that column is `NULL` the account is set to `disabled` and the request is refused with `account_disabled`. Either way `role_demoted_no_mapping` is audited at warning severity with the previous and new role (`to: null` when disabled) and the provider.
5. Any change from 3 or 4 that would leave zero active accounts holding the admin *preset* SHALL NOT be applied; the account keeps its role, its `role_source` flips to `manual`, and `role_source_overridden` is audited with `reason: last_admin_protected`, so the outcome is stable instead of retried on every run. The resolver SHALL take the same accounts write intent the account-management path takes BEFORE it reads that count, and SHALL read it in the transaction the demotion then commits, so two replicas re-evaluating two mapping-managed admins at once cannot each see the other survive and both demote.
6. Changing `unknown_identity_role_id` SHALL NOT re-apply to existing accounts (it is consulted at provisioning only).

The identity's group set SHALL be stored on the identity row (`groups_json`) and written only when it differs. Nothing else about an existing account is changed by re-evaluation.

#### Scenario: A manually set role survives every rule

- **GIVEN** an account with `role_source=manual` holding the admin preset and a provider rule that would make it a viewer
- **WHEN** the identity signs in
- **THEN** the role and `role_source` are unchanged and no audit row is written

#### Scenario: A provider with no rules re-evaluates nothing (D10)

- **GIVEN** a trusted-header provider with zero rules and accounts holding the admin and viewer presets
- **WHEN** each identity signs in
- **THEN** no role, `role_source` or status changes, and no re-evaluation audit row is written — the rules above apply only once the provider has at least one rule

#### Scenario: The first rule re-evaluates the accounts the login method created

- **GIVEN** three accounts provisioned by the trusted-header provider (`role_source=mapping`, admin preset from `unknown_identity_role_id`) and `no_match_role_id` = viewer
- **WHEN** the operator adds `groups=platform → operator` and each identity signs in
- **THEN** the identity in `platform` becomes an operator, the other two become viewers with `role_demoted_no_mapping`, and any account a human had already changed (`role_source=manual`) is untouched

#### Scenario: skip_role_sync stops re-evaluation wholesale

- **GIVEN** rules exist and the provider has `skip_role_sync=true`
- **WHEN** an existing `role_source=mapping` account signs in
- **THEN** its role is unchanged and no audit row is written
- **AND** a brand-new identity is still provisioned with the role its matching rule names

#### Scenario: No match with no fallback disables the account

- **GIVEN** rules exist, `no_match_role_id` is `NULL`, and an existing `role_source=mapping` viewer matches none of them
- **WHEN** the identity signs in
- **THEN** the request is refused with `account_disabled`, the account's status is `disabled`, and `role_demoted_no_mapping` records `to: null`

#### Scenario: Re-evaluation never removes the last admin

- **GIVEN** the only active admin-preset account is `role_source=mapping` and matches no rule
- **WHEN** the identity signs in
- **THEN** the account keeps the admin preset, its `role_source` becomes `manual`, `role_source_overridden` records `reason: last_admin_protected`, and a later sign-in repeats neither the change nor the audit row

#### Scenario: The last-admin count is read behind the write intent

- **GIVEN** a re-evaluation that would move the account off the admin preset
- **WHEN** the resolver decides whether another active admin remains
- **THEN** it acquires the accounts write intent first and only then counts, so the answer cannot go stale before the demotion commits

#### Scenario: Changing the unknown-identity default is not retroactive

- **GIVEN** accounts provisioned while `unknown_identity_role_id` was the admin preset, and one rule exists that they all match
- **WHEN** an operator changes `unknown_identity_role_id` to the viewer preset
- **THEN** the existing accounts keep the role their matching rule gives them and none of them is demoted by the new default

### Requirement: The reverse proxy's group header is a deployment-topology setting

`CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER` SHALL name the header the trusted-header provider reads groups from, default `Remote-Groups`, optional, tier T1, validated by the same `normalize_dashboard_auth_proxy_header` as `CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER` (reserved and authentication headers refused). It SHALL additionally refuse a value equal, case-insensitively, to the identity header, which would turn the username into a group claim. It lives in the environment, not in the dashboard, because it must match the reverse-proxy configuration; splitting it across two layers is how installs end up with a header nobody strips. The settings surface budget and `.env.example` SHALL be raised in the same change, and the generated settings reference SHALL name it.

#### Scenario: Default needs no configuration

- **WHEN** an install sets nothing
- **THEN** the provider reads `Remote-Groups` and an install whose proxy sends no such header behaves exactly as before

#### Scenario: A dangerous or duplicate header name is refused at startup

- **WHEN** the setting is `Authorization`, or equal to `CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER` in any casing
- **THEN** the process fails to start with a configuration error naming the setting

### Requirement: The provider settings API names the headers it reads

`GET /api/auth-providers` SHALL return, in the `config` map of a `trusted_header` row, the two header names that provider reads: `identityHeader` and `groupsHeader`, from the running settings. They are read-only facts of the deployment topology, so `PATCH /api/auth-providers/{id}` SHALL NOT accept them. Rows of any other kind SHALL keep an empty `config`. Without this the settings UI can only name the environment variables and not the values they carry, which is exactly the mismatch operators need to see.

#### Scenario: The reverse-proxy row carries both header names

- **WHEN** an admin lists the sign-in providers on an install with the defaults
- **THEN** the `trusted_header` row's `config` is `{"identityHeader": "Remote-User", "groupsHeader": "Remote-Groups"}` and the `password` row's `config` is empty

#### Scenario: They cannot be written

- **WHEN** a `PATCH /api/auth-providers/{id}` body carries `config`
- **THEN** the request is refused as an unknown field, because the request model forbids extras

### Requirement: Role mapping API

`GET /api/role-mappings` (`security:write`) SHALL list every rule as `{id, provider, providerKey, claimName, claimValue, roleId, priority, createdAt, updatedAt}` ordered by descending priority, optionally filtered by `provider` and `providerKey`. `POST /api/role-mappings` `{provider, providerKey, claimName, claimValue, roleId}` SHALL append a rule at the lowest priority, `PATCH /api/role-mappings/{id}` `{claimValue?, roleId?}` SHALL edit one, `DELETE /api/role-mappings/{id}` SHALL remove one and close the gap, and `PUT /api/role-mappings/order` `{provider, providerKey, ids}` SHALL set the whole order (first id wins). Every route, reads included, requires an attributable account (else `409 admin_account_required`) and, being `security:write`, every mutation additionally requires a step-up recorded within the last five minutes; every role handed out SHALL pass `resolve_assignable_role` and `assert_can_delegate` against the caller's grants (`422 role_not_assignable`, `403 insufficient_delegation`).

The delegation check SHALL cover the role a rule ALREADY hands out as well as any new one: `PATCH` (whatever it changes), `DELETE`, and every rule whose position a `PUT …/order` changes SHALL be refused `403 insufficient_delegation` unless the caller could have handed out that rule's current role. Retargeting, deleting or promoting a rule is the same delegation as writing it — otherwise a `security:write` holder that may not grant admin could point an admin-granting rule at a group it belongs to. This check SHALL use the role row as it is, so a rule whose role has since stopped being assignable still answers `403 insufficient_delegation` and not `422 role_not_assignable`.

Refusals: unknown id `404 mapping_not_found`; an unknown provider row `404 provider_not_found`; a `claimName` outside the supported set `422 unknown_claim`; a second rule with the same `(provider, providerKey, claimName, claimValue)` `409 mapping_exists`; more than 100 rules for one `(provider, providerKey)` `409 mapping_limit_reached`; an `ids` list that is not exactly the current rule set `409 order_stale`. Because every renumber plans from a snapshot the write read first, a concurrent writer that invalidates that snapshot makes the renumber collide with `UNIQUE(provider, provider_key, priority)`; such a collision SHALL be answered `409 order_stale` with the write rolled back, never as an unhandled error.

Every write SHALL audit `role_mapping_changed` (target `role_mapping`) with the operation, the provider, the claim and the role slug — including a delete and an edit that changes only the claim value, which SHALL read the role off the rule before changing it, so the audit record can always say what the rule granted — and SHALL invalidate the provider registry on every replica so the identity cache cannot keep refusing an identity a new rule now admits.

`GET /api/role-mappings/assignable-roles` (`security:write`, attributable account) SHALL list, as `{id, slug, name, description, kind, locked}`, exactly the roles this caller may point a rule or a provider default at: assignable to people and within the caller's own grants, by the same two rules the writes apply. The full roles list is `users:manage`, a different permission from the one that gates these rules, so without this read a custom role holding `security:write` could edit the rules but never name or choose the role any of them gives. It SHALL NOT report how many people hold a role.

#### Scenario: Adding the first rule

- **WHEN** an admin posts `{provider: "trusted_header", providerKey: "default", claimName: "groups", claimValue: "platform", roleId: <operator>}`
- **THEN** the rule is stored at priority 1, `role_mapping_changed` names the caller, and the next sign-in of a `platform` identity is an operator

#### Scenario: Handing out a role above the caller's own grants

- **GIVEN** a caller whose role holds `security:write` but not the admin grants
- **WHEN** it creates a rule pointing at the admin preset
- **THEN** the response is `403 insufficient_delegation` and no row is written

#### Scenario: A rule it could not have written, it cannot edit either

- **GIVEN** the same caller and an existing rule that hands out the admin preset
- **WHEN** it changes only that rule's claim value, changes its role, deletes it, or moves it in the order
- **THEN** every one of those is refused `403 insufficient_delegation`, the rule is unchanged and the order is unchanged
- **AND** the same refusal is given when that role has meanwhile stopped being assignable
- **AND** the caller's own viewer rule stays editable, and an order that moves nothing it may not touch is accepted

#### Scenario: A concurrent writer asks for a reload, not a 500

- **GIVEN** two writes to one provider's rules whose renumbers overlap
- **WHEN** the second one's snapshot is stale and its renumber collides on `UNIQUE(provider, provider_key, priority)`
- **THEN** the response is `409 order_stale`, the write is rolled back, and no row was left renumbered

#### Scenario: The audit row names the role on every write

- **GIVEN** a rule that hands out the operator preset
- **WHEN** it is edited by claim value only, and another rule is deleted
- **THEN** both `role_mapping_changed` rows carry the role the rule granted

#### Scenario: The caller can name the roles it may hand out

- **GIVEN** a caller whose role holds `security:write` but not `users:manage`
- **WHEN** it reads `GET /api/role-mappings/assignable-roles`
- **THEN** it receives the roles it may delegate (never the admin preset, never a non-assignable one) with their names
- **AND** an admin caller receives every assignable preset

#### Scenario: Even reading needs an attributable account

- **GIVEN** a principal with no account behind it (the implicit local admin)
- **WHEN** it lists the rules or the roles it may hand out
- **THEN** both are refused `409 admin_account_required`

#### Scenario: Editing requires a fresh credential

- **GIVEN** a signed-in admin whose last step-up was more than five minutes ago
- **WHEN** it deletes a rule
- **THEN** the response is `403 step_up_required` and the rule still exists

#### Scenario: Duplicate rule refused

- **GIVEN** `groups=platform → operator` exists
- **WHEN** another `groups=platform` rule is posted for the same provider
- **THEN** the response is `409 mapping_exists`

### Requirement: The access summary counts role mappings

`access_summary.role_mappings` SHALL be the real number of rows in `dashboard_role_mappings`, counted through the dashboard-auth repository like `custom_roles`, so that adding the first rule flips the disclosure tier to `enterprise` and the collapsed Organisation line to its status summary. It stays absent for a principal without `users:manage`, like the rest of the summary.

#### Scenario: First rule flips the tier

- **GIVEN** an install with no rules whose summary reports `roleMappings: 0`
- **WHEN** one rule is created and the session is refreshed
- **THEN** the summary reports `roleMappings: 1` and the derived tier is `enterprise`

## MODIFIED Requirements

### Requirement: The trusted-header provider yields a case-folded identity

`TrustedHeaderProvider` SHALL produce an identity only from the singular, trusted-peer header the existing request-auth resolution accepts, with `subject` = the trimmed header value case-folded, `display_name` = the trimmed raw value, `email` = the value when it parses as an e-mail address (else null), and `groups` = the values of the configured groups header. The groups header SHALL be honoured only when it appears exactly once on the request; its value is split on commas, each item trimmed, empty items dropped, items deduplicated case-insensitively, and the set bounded (at most 100 items of at most 200 characters, extras dropped). A header that appears more than once yields an EMPTY group set — a duplicate means something upstream is not stripping a client-supplied copy, and failing closed demotes a person instead of admitting them. The group set SHALL travel with the request-auth value the request path already resolves, so the resolver sees the same groups on the real request path as on a directly resolved identity.

#### Scenario: Two spellings, one identity

- **WHEN** the proxy sends `Alice@Example.com` and later `alice@example.com`
- **THEN** both resolve to the identity `(trusted_header, default, alice@example.com)` with e-mail `alice@example.com`

#### Scenario: Groups arrive comma-separated

- **WHEN** the proxy sends `Remote-Groups: platform, Staff , platform`
- **THEN** the identity carries the groups `platform` and `staff`

#### Scenario: A duplicated groups header yields no groups

- **WHEN** the request carries two `Remote-Groups` headers
- **THEN** the identity carries no groups and is treated as matching no `groups` rule

### Requirement: Identity resolution is one ordered path

Every provider's identity SHALL be mapped to an account by the same resolver, in this order: (1) an existing `dashboard_identities` row for `(provider, provider_key, subject)` → its account (a `disabled` account is refused with `account_disabled`; an `invited` one with `identity_not_provisioned`), after which the re-evaluation rules apply; (1.5) an `invited` account whose live invite (an SSO-only invite is live until consumed or revoked; the oldest match wins) carries exactly this triple in `expected_provider/expected_provider_key/expected_subject` → the identity row is created, the account becomes `active` (its `session_generation` is bumped), the invite is consumed with a compare-and-set on its liveness, and `identity_linked` is audited with `via: invite`; (2) when the provider's `link_by_email` is true and the identity carries an e-mail that equals an active account's normalised e-mail → the identity row is created and `identity_linked` (`via: email`) audited; (3) otherwise just-in-time provisioning: the provider's role mappings decide the role — the highest-priority matching rule wins, and only when no rule matches (including when the provider has no rules at all) is `unknown_identity_role_id` used; with that role NULL (or pointing at a missing role) the identity is refused with `identity_not_provisioned` and `login_failed` is audited at warning severity with `reason: unknown_identity`, `method`, `provider`, `provider_key`, `subject`, `email` and `groups`; with a role the account is created `active`, `role_source=mapping`, `display_name` = the identity's display name, `email` = the identity's e-mail unless another account already holds it, `last_login_at` set, together with its identity row (carrying the group snapshot), auditing `user_created` (actor: no account, `auth_method` = the provider kind, details `jit: true` and the identity) and `identity_linked` (`via: jit`). Accounts MUST never be resolved by username. A concurrent first request for the same identity MUST end with one account (the loser re-reads the winner's identity row).

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

#### Scenario: A rule beats the unknown-identity default at provisioning

- **GIVEN** `unknown_identity_role_id` is the viewer preset and a rule `groups=platform → operator`
- **WHEN** an unknown identity in `platform` makes its first request
- **THEN** the account is created as an operator with `role_source=mapping`, while an unknown identity in no group is created as a viewer

### Requirement: Resolution is throttled per identity

The resolution result SHALL be cached per `(provider, provider_key, subject)` for the users-cache TTL (5 s): within the TTL a repeated request performs no resolver database work and no audit write. A cached success names only the account id; the request path MUST re-read the account through the users cache so a disable or role change takes effect within the TTL. Re-evaluation of an existing account happens inside that same run and therefore inherits the throttle — this is what "a sign-in" means for a header install, where the identity is presented on every request. A run that finds nothing to change (same winning role, same status, same group snapshot) MUST issue no UPDATE and write no audit row, so a busy install does not fight an administrator's edit or grow the audit log. A write that changes how identities resolve — any role mapping write, any provider update — MUST invalidate the cache on every replica.

#### Scenario: One resolver run per TTL

- **WHEN** the same identity makes four requests within the TTL
- **THEN** the resolver runs once and every request is served

#### Scenario: Nothing changed, nothing written

- **GIVEN** an account whose role already equals the one its matching rule names
- **WHEN** its identity signs in on ten consecutive TTLs
- **THEN** no UPDATE and no audit row is produced by re-evaluation

#### Scenario: A new rule takes effect on the next run

- **GIVEN** an identity refused as `identity_not_provisioned` and cached as refused
- **WHEN** an operator adds a rule that matches it
- **THEN** the cache is invalidated by that write and the identity's next request is admitted
