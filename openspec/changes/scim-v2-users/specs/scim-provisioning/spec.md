## ADDED Requirements

### Requirement: The SCIM 2.0 user endpoint

The system SHALL serve `/scim/v2/Users` as its own router, registered with the other routers and therefore ahead of the single-page fallback, answering `application/scim+json` on every response. It SHALL support `POST` (create), `GET /{id}` (read), `PUT /{id}` (replace), `PATCH /{id}` (modify) and `GET` (list). A user resource SHALL carry `schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"]`, `id` (the dashboard account id), `externalId` (the identity's subject), `userName` (the value the identity provider pushed, not the local account name), `active`, `displayName`, `emails`, `roles` (the account's role slug, or empty when the role is not one this surface hands out) and `meta` with `resourceType: "User"`. Listing SHALL accept `filter=userName eq "<value>"` and nothing else, SHALL accept one-based `startIndex` and `count` with `count` clamped to a server maximum rather than refused, and SHALL answer a `ListResponse` with `totalResults`, `startIndex`, `itemsPerPage` and `Resources`. Both the read and the list SHALL be restricted to accounts holding a `scim` identity in the calling token's namespace, and SHALL include accounts whose status is `disabled` or `invited` so that a deprovisioned person stays rediscoverable. `PATCH` SHALL accept `op` case-insensitively, SHALL support `replace` and `add` on the attributes above, and SHALL coerce `active` from a JSON boolean and from the strings `"true"`/`"false"` in any casing, because identity providers send both. A `PATCH` SHALL be folded into the attributes of a user resource and then read by the same request model that reads a `PUT` body, so the two verbs cannot disagree about a maximum string length, a multi-valued cardinality cap, the `primary` flag on `emails` or the at-most-one rule on `roles`: a value one verb refuses SHALL be refused identically, with the same status and `scimType`, when the other verb carries it. An attribute the resource does not model SHALL be ignored rather than refused, whether it arrives as a `path` or as a key of a no-path operation, exactly as a `PUT` body ignores it, so that a modelled attribute batched beside it still applies. A `path` SHALL reach that recognition before any bound on it can refuse the request: the cap on a patch `path` SHALL be wide enough for the schema-qualified spellings identity providers send (the enterprise extension's `urn:…:enterprise:2.0:User:<attribute>` runs past seventy characters), because a `path` this resource drops is never stored and a cap tight enough to refuse a real one only converts a routine push — and the `active: false` batched beside it — into a `400`. An attribute named more than once in one request SHALL answer `400` with `scimType: "invalidSyntax"` naming it, because folding two writes of one attribute would have to discard one of them and the discarded one could be the value that should have been refused. `DELETE /scim/v2/Users/{id}` SHALL NOT be implemented; it SHALL answer `405` with an `Allow` header and a detail naming `PATCH {"active": false}`. Any unmatched path under `/scim/v2` SHALL answer a SCIM `404` and MUST NOT answer the single-page application document.

#### Scenario: A joiner is created and read back

- **WHEN** an identity provider posts `{"userName": "alice@example.com", "externalId": "ext-1", "active": true, "roles": [{"value": "viewer"}]}`
- **THEN** the response is `201` with a `Location` header, an `id` that is the new account's id, `userName` as pushed, and `active: true`
- **AND** `GET /scim/v2/Users?filter=userName eq "alice@example.com"` returns exactly that resource

#### Scenario: A deprovisioned person stays rediscoverable

- **GIVEN** that account after `PATCH {"active": false}`
- **WHEN** the identity provider filters on the same `userName`
- **THEN** the response is `200` with `totalResults: 1` and the resource reports `active: false`

#### Scenario: Deletion is not the way to deprovision

- **WHEN** `DELETE /scim/v2/Users/{id}` is called with a valid token
- **THEN** the response is `405` in the SCIM error envelope, names `PATCH {"active": false}`, carries an `Allow` header, and the account is unchanged

#### Scenario: An unmatched SCIM path is a SCIM 404

- **WHEN** a bearer-authenticated `GET /scim/v2/Groups` is made
- **THEN** the response is `404` in the SCIM error envelope and is not the single-page application document with `200`

#### Scenario: Only the supported filter is answered

- **WHEN** a list is requested with `filter=emails.value co "example.com"`
- **THEN** the response is `400` with `scimType: "invalidFilter"` and names the supported filter

#### Scenario: An unmodelled attribute does not take the operation beside it down

- **WHEN** a `PATCH` carries `{"active": false, "title": "Manager", "locale": "en-US"}` as one no-path operation, and separately a `path` of `title` beside a `path` of `active`
- **THEN** both answer `200`, the unmodelled attributes are ignored, and `active` is applied
- **AND** a `PUT` body carrying the same unmodelled attributes answers `200` as well

#### Scenario: A schema-qualified path is long, and still only ignored

- **WHEN** a `PATCH` names `urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:manager.displayName` beside `active: false`
- **THEN** the response is `200`, the extension path is ignored, and the account is deactivated
- **AND** a `path` past the request model's bound answers `400` with `scimType: "invalidSyntax"`

#### Scenario: A patch meets the caps a replace meets

- **WHEN** a `PATCH` sets `displayName` to 200 characters, and a `PUT` sets it to the same value
- **THEN** both answer `400` with `scimType: "invalidSyntax"`, and the stored value is unchanged — which on PostgreSQL is the difference between a refusal and a `500` from a `varchar(128)` column
- **AND** a `PATCH` whose `emails` value is `[{"value": "alias@example.com"}, {"value": "real@example.com", "primary": true}]` stores the same address a `PUT` carrying that array stores

#### Scenario: One attribute, named twice, is a refusal

- **WHEN** a `PATCH` carries one operation setting `roles` to `admin` and a second setting `roles` to `viewer`
- **THEN** the response is `400` with `scimType: "invalidSyntax"` naming `roles`, and the account's role is unchanged
- **AND** no value in the request has been applied in place of another

### Requirement: SCIM bearer tokens are hashed rows with a label

The system SHALL store SCIM credentials in `dashboard_scim_tokens` with `id`, `label`, unique `token_hash`, non-secret `token_prefix`, `provider_key`, `created_at`, nullable `created_by_user_id` (SET NULL on account deletion), nullable `last_used_at` and nullable `rotated_at`, created by one migration parented on the current single alembic head and leaving exactly one head. The plaintext SHALL be at least 256 bits of `secrets`-grade randomness behind a short non-secret marker, SHALL be stored only as `sha256(plaintext).hexdigest()`, and SHALL be returned exactly once — in the response to issue and to rotate — and never by a list, a read, a later response, a log line or an audit row. Issue and rotate SHALL require `security:write`, SHALL inherit the step-up requirement that covers every non-safe `security:write` request, and SHALL additionally require `assert_can_delegate(principal.grants, ADMIN_GRANTS)`, answering `403 insufficient_delegation` otherwise; revoke SHALL require `security:write` alone, because narrowing is never gated. The label SHALL be trimmed before it is bounded, not after, so that the value the length rules admit is the value that is stored and a whitespace-only label answers `422` instead of creating an unnamed credential. Rotation SHALL replace `token_hash` and `token_prefix` on the same row and stamp `rotated_at`, so the id, the label and the last-sync history survive and the previous secret stops working the moment it commits. `last_used_at` SHALL be advanced by a monotonic guarded write (`WHERE last_used_at IS NULL OR last_used_at < :now`) so replicas may write out of order. Issue, rotate and revoke SHALL audit `scim_token_issued`, `scim_token_rotated` and `scim_token_revoked` with the row id and the label and never the secret or its digest.

#### Scenario: The plaintext is shown once and never again

- **WHEN** an admin issues a token
- **THEN** the `201` carries the plaintext once
- **AND** the token list and every later read carry only the label, the prefix and the timestamps, and the audit row carries neither the plaintext nor its digest

#### Scenario: A credential cannot be issued without a name

- **WHEN** an admin issues a token whose label is only whitespace
- **THEN** the response is `422 validation_error` and no row is written
- **AND** a padded label is stored trimmed

#### Scenario: Rotation kills the previous secret in place

- **GIVEN** a token that an identity provider is using
- **WHEN** an admin rotates it
- **THEN** the response carries a new plaintext once, the row keeps its id, label and `last_used_at`, `rotated_at` is stamped, the new secret authenticates, and the previous secret answers `401`

#### Scenario: A replayed secret from before the rotation is refused

- **GIVEN** a request that was captured with the pre-rotation secret
- **WHEN** it is replayed after the rotation commits
- **THEN** the response is `401` and nothing in the detail distinguishes a rotated secret from an unknown one

#### Scenario: Issuing is an admin-level delegation

- **GIVEN** a session holding `security:write` but not the admin preset's grants
- **WHEN** it issues or rotates a SCIM token
- **THEN** the response is `403 insufficient_delegation` and no row is written
- **AND** the same session may revoke an existing token

### Requirement: A SCIM token authenticates SCIM and nothing else

A SCIM token SHALL be verified by hashing the presented value and fetching the row by equality on the unique digest index, then confirming the fetched row with a constant-time comparison (`hmac.compare_digest`) before it is trusted. The token SHALL NOT appear in any log line, audit detail, error detail, metric label or response body. The dependency SHALL return a frozen token record carrying the row id, the label and the `provider_key`, SHALL NOT construct a `DashboardPrincipal`, and SHALL NOT set `request.state.dashboard_principal`; no route on this router SHALL carry `validate_dashboard_session` or a dashboard permission dependency, and no response from this router SHALL set a cookie. A dashboard session cookie SHALL NOT authenticate this surface and a proxy API key SHALL NOT authenticate it. The `provider_key` used for every lookup, insert and resource resolution SHALL be read from the token row and never from the request body, a header or a path segment, and a SCIM `id` naming an account without a `scim` identity in that namespace SHALL answer `404`.

#### Scenario: The router carries no dashboard authority

- **WHEN** every route registered under `/scim/v2` is inspected
- **THEN** each carries the SCIM bearer dependency
- **AND** none carries `validate_dashboard_session` or a dashboard permission dependency, and none sets a cookie

#### Scenario: A dashboard credential is not a SCIM credential

- **WHEN** a valid dashboard session cookie, and separately a valid proxy API key presented as a bearer, are used against `/scim/v2/Users`
- **THEN** both answer `401` in the SCIM error envelope

#### Scenario: A SCIM credential is not a dashboard credential

- **WHEN** a valid SCIM token is presented as a bearer to a dashboard route and as a proxy API key
- **THEN** both are refused and no dashboard session is created

#### Scenario: No confusion between two tokens

- **GIVEN** two token rows whose `provider_key` differs, and an account provisioned under the first
- **WHEN** the second token reads that account by its SCIM id, or filters for its `userName`
- **THEN** the read answers `404` and the filter answers `totalResults: 0`

#### Scenario: The secret never reaches a record

- **WHEN** a request with a valid token and a request with an invalid one are both served
- **THEN** neither the presented value nor its digest appears in any log line, audit row or response body

### Requirement: Deprovisioning runs the shared deactivation

`active: false`, by `PUT` or by `PATCH`, SHALL call the shared `deactivate_user()` with an actor whose `auth_method` is `scim` and whose `source` names this surface, so the whole cascade applies: the account's sessions end through the `session_generation` bump, every active key it owns becomes inactive with `deactivated_reason='owner_disabled'`, the key cache entries are dropped, and `user_disabled` and `user_keys_deactivated` are audited. A live invite for the target SHALL be deleted in the same transaction while the account row is kept, and an account whose status is `invited` SHALL move to `disabled` rather than being deleted, because `userName eq` has to keep finding it. The call SHALL pass the qualifying break-glass post-state predicate; when the resulting state would leave zero qualifying accounts the whole transaction SHALL roll back — including the key cascade — and the response SHALL be SCIM `409` naming the account, with `scim_deprovision_refused` audited exactly once by the shared function and NOT again by the route. A deprovision that would leave zero active admin-preset accounts SHALL answer `409` naming that invariant instead, distinguished by re-reading the counts when the conditional write matches no row. A successful deprovision SHALL audit `scim_user_deprovisioned` with the account and the token row id, alongside the `user_disabled` and `user_keys_deactivated` rows the shared function writes. Any attribute change the same request carries — `displayName`, `userName`, `emails`, `roles` — SHALL still be pending when the shared function is called and SHALL be committed by that function's own commit, so a deprovision refused by either invariant takes the whole request back with it rather than leaving the renamed, re-addressed or re-roled account behind; no part of a refused request SHALL be observable afterwards. Deprovisioning an account that is already inactive SHALL answer `200` with the current resource and SHALL write and audit nothing, including the identity row's `last_seen_at` stamp, which SHALL be advanced only beside a change that is actually written. The lazy expired-invite purge SHALL NOT be called from this path, because it commits when it purges and rolls back when it does not, and either would break the transaction the guard depends on.

#### Scenario: A leaver loses sessions and keys

- **GIVEN** an active SCIM-provisioned account that owns two active keys and one key an admin revoked by hand
- **WHEN** the identity provider pushes `active: false`
- **THEN** the response is `200` with `active: false`, the account's next dashboard request answers `401`, both active keys are inactive with `deactivated_reason='owner_disabled'`, the hand-revoked key keeps `manual`, and `scim_user_deprovisioned` is audited

#### Scenario: The last qualifying break-glass account is not deprovisioned

- **GIVEN** `local_login_policy` is `break_glass_only` and the only qualifying break-glass account is SCIM-managed and owns an active key
- **WHEN** the identity provider pushes `active: false` for it
- **THEN** the response is `409` in the SCIM error envelope naming the account, the account stays active, its key stays active, and exactly one `scim_deprovision_refused` row is audited

#### Scenario: A not-yet-accepted joiner is deprovisioned

- **GIVEN** an `invited` account whose invite is `sso_only` and therefore never expires
- **WHEN** the identity provider pushes `active: false`
- **THEN** the invite row is gone, its link answers `404`, the account row remains with status `disabled`, and `userName eq` still finds it

#### Scenario: A replayed deprovision is idempotent

- **WHEN** the same `active: false` push is delivered twice
- **THEN** the second answers `200` with the same resource and writes no second audit row
- **AND** the identity row's `last_seen_at` is the value the first push left, because the second wrote nothing at all

#### Scenario: A refused deprovision leaves no part of its request behind

- **GIVEN** the last qualifying break-glass account while `local_login_policy` is `break_glass_only`
- **WHEN** one push carries a new `displayName`, a new `userName`, a new primary e-mail and `active: false`
- **THEN** the response is `409`, the account stays active with its keys, and the display name, the pushed `userName` and the address are all exactly what they were
- **AND** `userName eq` still finds the account under the name it was provisioned with

#### Scenario: The last administrator is not deprovisioned

- **GIVEN** the only active admin-preset account is SCIM-managed
- **WHEN** the identity provider pushes `active: false` for it
- **THEN** the response is `409` naming the administrator invariant rather than the break-glass one

### Requirement: Reactivation restores only the owner cascade

`active: true` on a disabled account SHALL run a principal-free twin of the shared deactivation that takes the accounts write intent before its first read, sets the status to `active`, bumps nothing the disable did not, commits, and only then restores keys — because the restoring `UPDATE` reads the owner's status in an `EXISTS` predicate and would restore nothing if it ran first. Exactly the keys whose `deactivated_reason` is `owner_disabled` SHALL be restored, with the reason cleared and their cache entries invalidated; keys whose reason is `manual` or `expired` SHALL NOT be touched. It SHALL audit `user_enabled` and `user_keys_reactivated` (details `count`) through the shared functions and SHALL NOT introduce a second event name. Reactivating an account that is already active SHALL answer `200` and write nothing, including the identity row's `last_seen_at`.

The enable direction SHALL be bounded by the same predicate that bounds a pushed role slug: a push SHALL NOT enable an account whose role — the role it will hold once this request's own role push, if any, has been applied — is one this surface would not itself hand out, that is, one `resolve_assignable_role` refuses or one that is admin-level over its resolved grants. That predicate SHALL be read before the commit that expires the row it reads. Such a push SHALL answer SCIM `409` with no `scimType`, naming the account and not its role, because the resource deliberately reports no `roles` for exactly these accounts; the account SHALL stay disabled, every key its disable cascaded SHALL stay inactive, and `scim_provision_refused` SHALL be audited with reason `account_not_provisionable`, the account and the token row id. A push carrying `active: true` for an account that is already active is not an enable and SHALL NOT be refused by this rule, so a directory that re-pushes an administrator's whole resource on a schedule is not answered with a permanent error.

#### Scenario: A push cannot re-enable an account it may not provision

- **GIVEN** a SCIM-provisioned account an administrator promoted to the admin preset with `force: true`, then disabled, cascading the key it owns to `owner_disabled`
- **WHEN** the identity provider pushes `active: true`
- **THEN** the response is `409` in the SCIM error envelope with no `scimType`, naming the account, the account stays `disabled` on the admin preset, its key stays inactive as `owner_disabled`, and `scim_provision_refused` is audited with reason `account_not_provisionable`
- **AND** no `user_enabled` row is written

#### Scenario: A returning person gets their own keys back, and nothing else

- **GIVEN** a disabled account with one key deactivated as `owner_disabled`, one revoked as `manual` and one `expired`
- **WHEN** the identity provider pushes `active: true`
- **THEN** the response is `200` with `active: true`, only the `owner_disabled` key is active again with its reason cleared, and `user_enabled` and `user_keys_reactivated` with `count: 1` are audited

#### Scenario: The status commits before the keys are considered

- **WHEN** the reactivation runs
- **THEN** the key restoration observes the owner as active and restores the eligible key in the same request, rather than leaving it inactive for a later call

### Requirement: A pushed role slug is bounded and never silently defaulted

A `roles` value SHALL be a role **slug**, trimmed and case-folded before lookup, and SHALL carry at most one entry. The slug SHALL be accepted only when `resolve_assignable_role` accepts it **and** the role is not admin-level (`is_admin_level` over its resolved grants is false), so a provisioning push can neither create nor promote to an administrator; promotion stays a human action on `PATCH /api/dashboard-users/{id}`, where the delegation check has a real principal. Every rejected slug SHALL answer SCIM `400` naming the value verbatim, with a detail that distinguishes an unrecognised slug, a recognised slug this release does not assign to accounts (`guest`, and `member` until member self-service ships), and a recognised assignable slug that is admin-level, and SHALL audit `scim_provision_refused` with that reason, the slug, the external id and the token row id. A refused slug SHALL NOT be replaced by a default in either direction — neither raised nor silently lowered — and the account SHALL NOT be created or modified by that request. A create carrying no `roles` value at all SHALL provision the least-privileged assignable preset, `viewer`, which is a stated default for an absent attribute and not a fallback for a refused one — no rejected value SHALL ever reach it. A pushed slug SHALL change the role only when the account is being created, or when its `role_source` is `scim` **and** its current role is one this surface provisions by the same predicate above — a role it may not hand out is a role it may not take away, or one request could demote the last administrator and disable it in the same breath and the last-admin guard, which reads the role that write leaves behind, would never fire. On an account a human pinned to `role_source=manual`, and on an account holding a role this surface does not provision, the role SHALL be left exactly as it is and the rest of the push SHALL still apply. The skip SHALL be audited once rather than on every push, as `scim_role_push_ignored` at WARNING carrying the account, the pushed slug and the reason (`role_source_manual` or `role_not_provisionable`); that row is the marker, read back before another is written, so a directory that re-pushes on a schedule produces exactly one. A request that is refused for any other reason SHALL NOT write it, because the push it belonged to did not happen.

#### Scenario: An unknown slug is a provisioning error

- **WHEN** a create pushes `roles: [{"value": "team-lead"}]`
- **THEN** the response is `400` with `scimType: "invalidValue"`, the detail contains `team-lead`, no account is created, and `scim_provision_refused` is audited with reason `unknown_role`

#### Scenario: An identity provider cannot provision an administrator

- **WHEN** a create or an update pushes `roles: [{"value": "admin"}]`
- **THEN** the response is `400` naming `admin` and saying an administrator assigns it by hand, no account is created or promoted, and the refusal is audited with reason `role_not_provisionable`
- **AND** the account is not created as a viewer instead

#### Scenario: A recognised but unassignable slug says which it is

- **WHEN** a create pushes `roles: [{"value": "member"}]`
- **THEN** the response is `400` naming `member` and stating that this release does not provision it, with a reason distinct from an unrecognised slug

#### Scenario: Casing is not a provisioning error

- **WHEN** a create pushes `roles: [{"value": "Viewer"}]`
- **THEN** the account is created on the viewer role

#### Scenario: A hand-pinned role is not moved by a push

- **GIVEN** a SCIM-provisioned account an admin took over with `force: true`, leaving `role_source=manual`
- **WHEN** the identity provider pushes a different assignable slug
- **THEN** the response is `200`, the role and `role_source` are unchanged, the rest of the push is applied, and exactly one `scim_role_push_ignored` row with reason `role_source_manual` exists across repeated pushes

#### Scenario: A role this surface may not grant is one it may not take away

- **GIVEN** an account whose `role_source` is `scim` and whose role has become admin-level
- **WHEN** the identity provider pushes `viewer`
- **THEN** the response is `200`, the role is unchanged, and one `scim_role_push_ignored` row with reason `role_not_provisionable` is audited
- **AND** the same push combined with `active: false`, on an install where that account is the only active administrator, answers `409` naming the administrator invariant, leaves the role and the status unchanged, and writes no second skip row

### Requirement: SCIM identities are resolver rows and a userName never resolves an account

A SCIM create SHALL write a `dashboard_identities` row with `provider='scim'`, `provider_key` from the token row, and `subject` set to `externalId` exactly as pushed — not case-folded, because a subject is opaque — and SHALL resolve an existing person by that triple alone or by an open invite that expects exactly that triple, never by `userName`. The local account name SHALL be derived by the shared provisioning rules rather than a second copy of them: the subject slug, the `-2`/`-3` collision walk, `admin` treated as taken, and the bounded `IntegrityError` retry that re-checks whether the triple landed concurrently. The local name SHALL be assigned once; a later `userName` SHALL update what the identity provider calls the person and SHALL NOT rename the account. An e-mail another account already holds SHALL stay on the identity row with the new account's `email` left null; because `dashboard_users.email` is unique, that check SHALL be re-read on each retry rather than only before the first attempt, so an address claimed between the check and the commit is dropped by the retry instead of failing every remaining attempt on the same violation. The pushed `userName` SHALL be stored on the identity row and SHALL be what the resource reports and what `userName eq` matches, case-insensitively. A create whose `externalId` already has an identity row in this namespace SHALL answer `409` with `scimType: "uniqueness"` naming the existing resource id, and a `PUT` or `PATCH` that changes `externalId` SHALL answer `400` with `scimType: "mutability"`. SCIM SHALL NOT link an identity to an existing account by e-mail: that widening is a company sign-in provider setting whose write is gated on admin-level delegation, and a machine push carries no such consent. A create SHALL audit `scim_user_provisioned` with the account, the external id and the token row id, alongside the account-creation and identity-link rows the shared provisioning already writes.

#### Scenario: A userName that collides with a local account does not take it over

- **GIVEN** an existing local account named `alice.example.com`
- **WHEN** the identity provider provisions `userName: "alice@example.com"` with a new `externalId`
- **THEN** a new account named `alice.example.com-2` is created, the existing account is unread and unchanged, and the resource reports `userName: "alice@example.com"`

#### Scenario: A pushed `admin` userName cannot inherit the emergency account

- **WHEN** the identity provider provisions `userName: "admin"`
- **THEN** the account is named `admin-2` and the install's `admin` account is untouched

#### Scenario: An address another account holds stays on the identity

- **GIVEN** an existing account with e-mail `carol@example.com`
- **WHEN** a SCIM create pushes that address with a new `externalId`
- **THEN** a separate account is created with a null `email`, the address is recorded on the identity row, and the existing account is unchanged

#### Scenario: An address claimed between the check and the commit

- **GIVEN** a SCIM create whose pushed address is free when the provisioning path checks it
- **WHEN** another writer claims that address before the insert commits, so the insert raises on the unique index while the identity triple is still absent
- **THEN** the retry re-reads the address, provisions the account with a null `email`, and the address is recorded on the identity row

#### Scenario: The same joiner pushed twice

- **WHEN** a create repeats an `externalId` that is already provisioned in this namespace
- **THEN** the response is `409` with `scimType: "uniqueness"` and the detail names the existing resource id
- **AND** a `PUT` that changes an existing resource's `externalId` answers `400` with `scimType: "mutability"`

#### Scenario: A rename at the identity provider does not rename the account

- **GIVEN** an account provisioned as `alice.example.com`
- **WHEN** the identity provider replaces the resource with `userName: "alice.smith@example.com"`
- **THEN** the account name is unchanged, the resource reports the new `userName`, and `userName eq` now matches the new value

### Requirement: The SCIM surface is machine-to-machine

`/scim/v2` SHALL be outside the dashboard CSRF middleware's protected prefix and SHALL always be bearer-authenticated, so it is exempt twice over; the middleware SHALL NOT be widened to name it, and the exemption SHALL be pinned by a regression test rather than by a code branch. The routes SHALL NOT enter the step-up gate, which has no account to re-verify. Requests SHALL be rate limited in two buckets — a coarse one keyed on the peer address and a narrow one keyed on the **token row id**, never on the token or its digest, because the attempt key is stored in clear and the digest is the verifier — and the budget SHALL be spent and committed **before** the deactivation transaction is opened, because the limiter commits and a commit releases the accounts write intent the break-glass guard depends on. The request body SHALL be bounded at the route rather than by the global ingress budget: a declared `Content-Length` over the route bound SHALL answer the SCIM `413` before the body is read, every string attribute SHALL carry a maximum length, and the number of `Operations` and of multi-valued entries SHALL be capped.

#### Scenario: A cross-site browser request is not evaluated for CSRF

- **WHEN** a `POST /scim/v2/Users` arrives with `Sec-Fetch-Site: cross-site`, no origin allowance and a valid bearer
- **THEN** it is served on its merits and the CSRF middleware does not refuse it
- **AND** the middleware's protected prefix is unchanged

#### Scenario: An oversized body is refused before it is read

- **WHEN** a request declares a `Content-Length` above the route bound
- **THEN** the response is `413` in the SCIM error envelope, naming the bound, and no account is written

#### Scenario: Rate limiting keys on the row, not the secret

- **WHEN** the per-token budget is exhausted
- **THEN** further requests answer `429` in the SCIM error envelope with `Retry-After`
- **AND** no stored attempt key contains the token or its digest

#### Scenario: The budget is spent outside the guarded transaction

- **WHEN** a deprovision is served
- **THEN** the rate-limit write commits before the accounts write intent is taken, and the break-glass count and the conditional write remain in one uncommitted transaction

### Requirement: Every SCIM refusal answers in the SCIM error envelope

Every refusal from `/scim/v2`, including one raised before routing or by request validation, SHALL answer `application/scim+json` with `{"schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"], "status": "<code>", "detail": "<sentence>"}` and SHALL carry `scimType` exactly where RFC 7644 defines a keyword for the case — `invalidValue`, `invalidSyntax`, `invalidPath`, `invalidFilter` and `mutability` on `400`, `uniqueness` on the duplicate `externalId` `409` — and no product-specific member. The dashboard error envelope SHALL NOT be used. A missing, malformed, unknown, rotated-away or revoked bearer SHALL answer `401` with `WWW-Authenticate: Bearer` and one indistinguishable detail for all five cases. The `409`s that carry this product's account invariants SHALL omit `scimType`, because the specification defines keywords only for `400` and none of them means "the last emergency account"; the administrator-facing reason lives in the audit row.

#### Scenario: Validation failures are SCIM-shaped

- **WHEN** a create is posted with a body the request model rejects
- **THEN** the response is `400` in the SCIM error envelope with `scimType: "invalidSyntax"` and is not FastAPI's `{"detail": [...]}` shape

#### Scenario: An unauthenticated request says nothing useful

- **WHEN** requests are made with no bearer, with a syntactically invalid bearer, with an unknown one and with one that a rotation replaced
- **THEN** all four answer `401` with the same detail and a `WWW-Authenticate: Bearer` header, and none reveals which case it was

#### Scenario: An invariant refusal carries no invented keyword

- **WHEN** a deprovision is refused by the break-glass invariant
- **THEN** the body carries `status: "409"` and a detail naming the account, and carries no `scimType`

### Requirement: The access summary counts SCIM tokens

`access_summary.scim_tokens` SHALL be the real number of rows in `dashboard_scim_tokens`, counted through the dashboard-auth repository like `custom_roles` and `role_mappings`, so that issuing the first token flips the disclosure tier to `enterprise` and the collapsed Organisation line to its status summary. It stays absent for a principal without `users:manage`, like the rest of the summary. The organisation-configured predicate SHALL count SCIM tokens as configuration, so the collapsed line stops reading the unconfigured one-liner once a token exists.

#### Scenario: The first token flips the tier

- **GIVEN** an install with no tokens whose summary reports `scimTokens: 0`
- **WHEN** one token is issued and the session is refreshed
- **THEN** the summary reports `scimTokens: 1`, the derived tier is `enterprise`, and the collapsed Organisation line is a status summary

#### Scenario: Revoking the last token returns the line

- **WHEN** the only token is revoked and nothing else is configured
- **THEN** the summary reports `scimTokens: 0` and the collapsed line is the plain sentence again
