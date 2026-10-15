## ADDED Requirements

### Requirement: The OIDC provider is configured in the dashboard and stored encrypted

The `oidc` provider's connection settings SHALL live in `dashboard_auth_providers.config_encrypted` as one JSON document sealed with the existing `TokenEncryptor`, and SHALL have no environment variable, no `Settings` field, no `.env.example` line and no configuration-tier entry (owner decision D6). The document SHALL carry `issuer`, `discovery_url`, `client_id`, `client_secret`, `redirect_uri` and the four configurable claim names `subject_claim` (default `sub`), `email_claim` (default `email`), `name_claim` (default `name`) and `groups_claim` (default `groups`).

Every URL in the document SHALL be validated when it is written and refused with `422 invalid_provider_config` naming the field when it is not `https`, carries a userinfo component or a fragment, or names a host that is an IP literal in a loopback, link-local, private or unique-local range, or is `localhost`, a name under `.localhost`, or `metadata.google.internal`. The host SHALL be recognised as an address in **every spelling the resolver accepts**, not only the canonical dotted quad: the single-integer, octal, hexadecimal and short forms (`2130706433`, `0177.0.0.1`, `0x7f.0.0.1`, `127.1`, `2852039166`) name the same loopback and link-local addresses and SHALL be refused identically. A name the address parser cannot read SHALL remain a name; the check resolves no DNS and therefore still makes no claim against rebinding. A URL the URL parser itself cannot read (an unterminated IPv6 literal, say) SHALL be refused the same way and SHALL NOT escape as a `500`, and so SHALL a URL whose **port** does not parse (`:port`, `:-1`, `:99999`) — that check is only performed if the lazily evaluated port is actually read at write time, and nothing downstream reads it, so an unchecked one would be stored and surface later as an unreachable identity provider instead of as the field that is wrong. The issuer and the `redirect_uri` SHALL additionally carry no query — the redirect URI for the sharper reason that RFC 6749 has the token request repeat it byte-for-byte and the identity provider compares its registered value the same way, so a query on it is a mismatch waiting to surface at the exchange instead of at the field. `redirect_uri` SHALL additionally end with the callback path, and SHALL be the only source of the redirect URI the flow sends: the request's `Host` header is never validated or rewritten by the proxy middleware, so a reflected redirect URI would be attacker-controllable.

The same rule SHALL gate every endpoint the **discovery document** advertises (`jwks_uri`, `authorization_endpoint`, `token_endpoint`), each validated before any of them is fetched, so the identity provider cannot name an address the operator could not have typed — the token endpoint being where the client secret is sent.

The migration and the test schema SHALL seed one `oidc/default` row with the same deterministic id and insert-ignore semantics as the other built-in rows, **disabled**, with `unknown_identity_role_id` NULL, `no_match_role_id` NULL and `link_by_email` false, so an install that never connects an identity provider is unchanged and a connected one denies unknown identities by default. There SHALL be no endpoint that creates a provider row.

A write SHALL replace the whole document rather than merge into it, and SHALL therefore require `client_secret` every time: a partial write that kept the stored secret while repointing the issuer would send the operator's credential to a server they had not yet typed it for. Re-writing a byte-identical document SHALL NOT count as a change.

The stored client secret SHALL never be returned by any API, written to any log line, or placed in any audit detail, and the token request that carries it SHALL not be logged. An audit row for a configuration write SHALL record only that the configuration changed, never any of its values.

#### Scenario: The connection settings are stored encrypted and never come back in clear

- **WHEN** an admin writes the OIDC connection settings
- **THEN** `config_encrypted` holds one sealed document, no column holds the client secret in clear, and no `Settings` field, `.env.example` line or tier entry was added

#### Scenario: An unsafe issuer is refused

- **WHEN** the issuer or discovery URL is written as a plain-HTTP URL, with a userinfo component, with a fragment, or with a host that is a loopback, link-local, private or unique-local address or `metadata.google.internal`
- **THEN** each is refused `422 invalid_provider_config` naming the field and nothing is stored

#### Scenario: A refused address is refused in every spelling

- **WHEN** the discovery URL names the loopback or metadata address in its single-integer, octal, hexadecimal, short or IPv4-mapped form (`2130706433`, `0177.0.0.1`, `0x7f.0.0.1`, `127.1`, `2852039166`, `[::ffff:127.0.0.1]`)
- **THEN** each is refused `422 invalid_provider_config` naming the field, exactly as the dotted quad is
- **AND** a public address written in the same non-canonical form, and any host name the address parser cannot read, is still accepted

#### Scenario: A URL the parser cannot read is a refusal, not a crash

- **WHEN** the issuer, discovery URL or redirect URI is written as `https://[broken`
- **THEN** the response is `422 invalid_provider_config` naming that field, not `500`, and nothing is stored
- **AND** the same holds for `https://idp.example.test:port`, whose port raises only when it is read

#### Scenario: The redirect URI carries no query

- **WHEN** the redirect URI is written as the callback path followed by `?next=/dashboard`
- **THEN** it is refused `422 invalid_provider_config` naming `redirect_uri` and nothing is stored

#### Scenario: The redirect URI comes from the row, not the request

- **GIVEN** a stored redirect URI
- **WHEN** a sign-in is started on a request carrying a forged `Host` header
- **THEN** the authorization request and the later token exchange both carry the stored value byte-identically, and the forged host appears nowhere

#### Scenario: The seeded row denies by default

- **WHEN** the migration runs on an empty database and again on one that already has the row
- **THEN** exactly one `oidc/default` row exists, it is disabled with `unknown_identity_role_id` NULL, and the second run changes nothing

### Requirement: Discovery documents and signing keys are cached, refreshed and survive key rotation

The provider SHALL fetch the discovery document and, from the `jwks_uri` it advertises, the signing keys, over the shared outbound HTTP session with an explicit ten-second timeout, `https` only, with redirects NOT followed, refusing a response that is not JSON, is larger than 256 KiB, or advertises more than twenty keys. Every bounded body SHALL be read to end of stream rather than from a single read, which returns only what has arrived: a document delivered across several network writes SHALL parse whole, and the size cap SHALL still stop the read one byte past its limit. The document's own `issuer` claim SHALL be required to equal the configured issuer exactly; the advertised `authorization_endpoint`, `token_endpoint` and `jwks_uri` SHALL pass the same scheme and host validation as the issuer but SHALL NOT be required to share its origin, because a target identity provider publishes its keys on a different host from its issuer and a rule operators must switch off is not a rule.

The document and the parsed key set SHALL be cached together per provider, keyed on a digest of the connection document itself, with a fifteen-minute positive lifetime. The key SHALL NOT be derived from the provider row's `updated_at`: that column has one-second resolution on SQLite, so two configuration writes inside one second would share an entry and a repointed provider would keep serving the previous issuer's endpoints — sending the new client secret to the previous identity provider's token endpoint, a leg that runs before any issuer check could refuse the answer.

An ID token whose `kid` is absent from the cached set SHALL trigger exactly one out-of-band refresh, rate-limited to once per sixty seconds per provider; when the `kid` is still absent, or the cooldown is in force, the token SHALL be refused rather than accepted unverified. A `kid` that the *currently cached* key set holds SHALL be answered from that key set before the cooldown is consulted, so that a refresh another caller has already performed serves every caller meeting the same rotation instead of only the first.

When a refresh fails the last successfully fetched key set SHALL keep serving for at most twenty-four hours, after which sign-in through that provider fails rather than trusting keys of unknown age. A failed refresh SHALL additionally record its own backoff, held separately from the last successful fetch, and further attempts SHALL be suppressed for its duration while the cached key set is served: without it a discovery endpoint that times out costs every start and every callback another full timeout, serialised behind the cache lock, so one broken endpoint stops a sign-in path whose token endpoint is healthy. The backoff SHALL NOT extend the twenty-four-hour ceiling, which is measured from the last successful fetch.

#### Scenario: A rotated key converges without an operator action

- **GIVEN** a cached key set and an identity provider that has begun signing with a newly published key
- **WHEN** the first ID token carrying the new `kid` arrives
- **THEN** one refresh is performed, the token verifies, and later tokens with that `kid` are served from the cache

#### Scenario: Forged key ids cannot be used to hammer the identity provider

- **WHEN** twenty callbacks arrive within a minute carrying ID tokens whose `kid` values are unknown
- **THEN** at most one refresh is performed, every one of the twenty is refused, and none is accepted unverified

#### Scenario: A mismatched or unreachable discovery document is refused

- **WHEN** the discovery document's `issuer` claim differs from the configured issuer, or the fetch answers a redirect, a non-JSON body, a body over 256 KiB, or a key set of more than twenty keys
- **THEN** the fetch is treated as a failure, no endpoint from that document is used, and no sign-in completes through it

#### Scenario: Two callbacks meet one rotation

- **GIVEN** two callbacks holding the same cached key set, both carrying the identity provider's newly rotated `kid`
- **WHEN** the first forces the refresh and the second asks a moment later
- **THEN** both verify against the refreshed key set, exactly one refresh is performed, and a `kid` that really is unknown still buys none

#### Scenario: A repoint inside one second is not served from the previous issuer

- **GIVEN** a cached discovery document for the configured issuer
- **WHEN** the connection is rewritten to another issuer without the row's `updated_at` advancing
- **THEN** the next fetch is made against the new document, and no endpoint of the previous issuer is used

#### Scenario: A brief outage does not lock everyone out

- **GIVEN** a cached key set and an identity provider that has become unreachable
- **WHEN** sign-ins continue for the next hour, and again twenty-five hours later
- **THEN** the first hour is served from the cached keys, and the attempt after the cap fails instead of trusting them

#### Scenario: A failing discovery endpoint is retried on a backoff

- **GIVEN** a cached key set whose positive lifetime has expired and a discovery endpoint that times out
- **WHEN** ten sign-ins arrive inside the backoff window
- **THEN** every one is served from the cached key set and none attempts a fetch, one attempt is made after the window, and the twenty-four-hour ceiling still ends it

### Requirement: The ID token is validated before any claim is trusted

The provider SHALL accept claims only from an ID token whose signature it has verified, and SHALL NOT read claims from an unverified payload or from a `userinfo` response. Verification SHALL select the candidate key from the cached key set by `kid`, and SHALL derive the permitted algorithms from **that key's own type** intersected with the identity provider's advertised `id_token_signing_alg_values_supported` and with the fixed allow-list `RS256`, `RS384`, `RS512`, `PS256`, `PS384`, `PS512`, `ES256`, `ES384`, `ES512`. The token header's `alg` SHALL NOT be an input to that choice, and `none` and every MAC algorithm SHALL be refused.

The token SHALL further be required to carry `iss` equal to the configured issuer, `aud` containing the configured client id (and, when `aud` holds more than one value, an `azp` equal to the client id), a non-empty `sub`, `exp`, `iat` and the `nonce` the flow sent. Time comparisons SHALL apply a fixed sixty-second clock-skew tolerance in both directions, expressed as a named constant and not as a setting. Independently of that tolerance, the token's `iat` SHALL NOT predate the flow's own creation time by more than the tolerance, so a token minted long ago cannot be presented against a flow started now however distant its `exp`.

#### Scenario: Algorithm confusion is refused

- **WHEN** an ID token arrives whose header names `none`, names a MAC algorithm while the selected key is an RSA public key, or names an algorithm outside the allow-list
- **THEN** each is refused, no claim from it is read, and no session is issued

#### Scenario: Wrong audience, wrong issuer, expired

- **WHEN** an otherwise well-signed ID token names another audience, names another issuer, carries several audiences without a matching `azp`, or expired more than sixty seconds ago
- **THEN** each is refused and the callback ends in the uniform failure

#### Scenario: Clocks a little apart still work

- **GIVEN** an identity provider whose clock is forty seconds ahead
- **WHEN** it issues an ID token whose `iat` is in the future by that much
- **THEN** the token is accepted, while one whose `iat` is two minutes in the future is refused

#### Scenario: An old token cannot be replayed against a fresh flow

- **GIVEN** a captured ID token minted an hour ago with a long expiry
- **WHEN** it is presented to complete a flow started a moment ago
- **THEN** it is refused because its `iat` predates the flow

### Requirement: OIDC claims become an identity for the shared resolver

The provider SHALL build one `ExternalIdentity` from the verified ID token and hand it to the same identity resolver every other provider uses, taking `provider` = `oidc` and `provider_key` = `default` so the existing role-mapping rules and identity rows match it. `subject` SHALL be the value of the configured subject claim **verbatim** — neither case-folded (an OIDC `sub` is case-sensitive by specification, unlike the trusted-header subject) nor trimmed, so that two subjects differing only in surrounding whitespace stay two identities rather than collapsing onto one account. A subject that is empty or whitespace-only, or longer than the subject column's 512 characters, SHALL be refused; those checks SHALL NOT rewrite the value that is looked up. `display_name` SHALL be the configured name claim truncated to the display-name column's width, `groups` SHALL be the configured groups claim's values trimmed and case-folded so they match the stored rules, and `email` SHALL be the configured e-mail claim when it parses as an address.

An `email_verified` claim that is **present** SHALL make the identity carry an e-mail only when its value is an explicit affirmative: the JSON boolean `true`, or the string `"true"` compared case-insensitively. Every other present value — `false`, `"false"`, `0`, `null`, a container — SHALL make the identity carry **no** e-mail, because the resolver would otherwise use a self-asserted address for e-mail linking, for the new account's address, for `email_domain` role-mapping rules and for the re-evaluation that re-applies those rules on every later sign-in. The string spelling SHALL be accepted because Google's documented ID token carries `"true"`, Apple documents the claim as string-or-boolean and Cognito emits strings for attributes mapped from an upstream provider; accepting only the boolean would drop the address of legitimately verified users at three of the identity providers this capability exists for. An **absent** claim SHALL carry the e-mail, as today.

The provider SHALL NOT create, link or re-evaluate accounts itself, SHALL NOT re-implement the just-in-time rules, and SHALL NOT emit the identity audit events the resolver already writes.

#### Scenario: Two spellings of a subject are two identities

- **GIVEN** an identity provider that issues the subjects `Alice` and `alice`
- **WHEN** each signs in
- **THEN** two distinct identities exist, unlike the trusted-header path where the two would be one
- **AND** the same holds for `alice` and ` alice `, whose difference is whitespace the lookup does not remove

#### Scenario: Claim names are configurable

- **GIVEN** a provider configured to read groups from a claim the tenant calls `roles` and the name from `preferred_username`
- **WHEN** an ID token carrying those claims is verified
- **THEN** the identity's groups and display name come from them, and the existing highest-priority rule decides the role

#### Scenario: An unverified e-mail is dropped

- **GIVEN** an ID token whose e-mail claim names an address and whose `email_verified` claim is false
- **WHEN** the identity is built
- **THEN** it carries no e-mail, matches no `email_domain` rule, and links to no existing account even with `link_by_email` on

#### Scenario: Only an affirmative counts as verified

- **WHEN** `email_verified` arrives as `"false"`, `"False"`, `0`, `"no"`, `null` or an empty container
- **THEN** each drops the address, exactly as the JSON boolean `false` does
- **AND** `true` and the string `"true"` in any casing keep it, while an absent claim keeps it as before

#### Scenario: An unknown identity with no matching rule is refused, by the resolver

- **GIVEN** the seeded OIDC row with `unknown_identity_role_id` NULL and no rule matching the identity's groups
- **WHEN** a person completes sign-in at the identity provider
- **THEN** no account is created, the request is refused as not provisioned, and exactly one `login_failed` row with `reason: unknown_identity` names the provider, the subject, the e-mail and the groups — written by the shared resolver, not by the provider

#### Scenario: An oversized or empty subject is refused

- **WHEN** a verified ID token carries an empty subject claim, or one longer than 512 characters
- **THEN** the sign-in is refused before any account lookup

### Requirement: Enabling the OIDC provider requires a recent test login by the acting admin

`dashboard_auth_providers` SHALL carry `test_login_user_id` (FK → `dashboard_users` ON DELETE SET NULL) and `test_login_verified_at`. A test sign-in that completes end to end SHALL stamp both with the acting admin and the current time and SHALL audit `oidc_test_login_succeeded` (target `auth_provider`, naming the provider and the subject it verified); it SHALL NOT create an account, write an identity row or issue a session, because a pre-flight that provisions is not a pre-flight.

A completed test sign-in SHALL stamp the row only while it still holds the connection document the round trip was actually made against. The flow state SHALL therefore carry a digest of that document; a callback whose provider no longer matches it SHALL end in the uniform failure without exchanging the authorization code, and the stamping write SHALL additionally be conditional on the stored document being unchanged since the callback read it. Without both, a configuration write that commits while the callback is out at the identity provider clears the proof and the stamp writes it straight back — against an issuer nobody has tested.

A `PATCH /api/auth-providers/{id}` that sets `enabled` true on an `oidc` row SHALL require a stamp naming the **acting** account and no older than ten minutes, and SHALL otherwise answer `409 oidc_test_login_required`; freshness SHALL be computed from the stored time on every read and never persisted as a deadline. Writing any connection field of the provider's configuration SHALL clear the stamp, so a proof obtained against one identity provider cannot enable a different one; changing a label or a role knob SHALL NOT. A successful enable SHALL consume the stamp, so it cannot silently re-enable a provider an operator has since turned off. The check SHALL run inside the same account write intent, and beside the same qualifying-break-glass gate, that the `enabled` transition already takes and holds until its commit.

#### Scenario: Enabling without a test login is refused

- **WHEN** an admin enables the OIDC row with no test login recorded, or with one recorded eleven minutes ago
- **THEN** both answer `409 oidc_test_login_required` and the row stays disabled

#### Scenario: One admin's test login does not arm another's enable

- **GIVEN** a successful test login by one admin a minute ago
- **WHEN** a different admin enables the row
- **THEN** the response is `409 oidc_test_login_required`, while the admin who ran the test succeeds

#### Scenario: Repointing the identity provider invalidates the proof

- **GIVEN** a successful test login a minute ago
- **WHEN** the issuer, discovery URL, client id, client secret or redirect URI is written and the row is then enabled
- **THEN** the response is `409 oidc_test_login_required`
- **AND** writing only the label leaves the proof standing

#### Scenario: A configuration written mid-flight is not what gets proved

- **GIVEN** a test sign-in started against one connection document
- **WHEN** the connection is rewritten before the callback returns
- **THEN** the callback ends in the uniform failure, no authorization code is exchanged, no stamp is written, and enabling still answers `409 oidc_test_login_required`
- **AND** a write that commits after the callback has read the row but before it stamps leaves the row unstamped too

#### Scenario: The proof is spent by the enable it armed

- **GIVEN** a successful enable that consumed the proof
- **WHEN** an admin disables the row and immediately enables it again
- **THEN** the second enable answers `409 oidc_test_login_required`

#### Scenario: Both gates hold, atomically

- **GIVEN** no qualifying break-glass account and no test login
- **WHEN** an admin enables the OIDC row
- **THEN** the request is refused, the row stays disabled, and the count and the write happen under one account write intent taken before the first read

## MODIFIED Requirements

### Requirement: Sign-in providers are rows with an implementation

The system SHALL keep one row per way of signing in in `dashboard_auth_providers` (`id`, `kind` ∈ {`password`, `trusted_header`, `oidc`}, `provider_key`, `enabled`, `label`, `config_encrypted`, `unknown_identity_role_id` FK → `dashboard_roles` ON DELETE SET NULL, `no_match_role_id` FK SET NULL, `link_by_email` default false, `skip_role_sync` default false, `idp_mfa_enforced` default false, `test_login_user_id` FK → `dashboard_users` ON DELETE SET NULL, `test_login_verified_at`, `created_at`, `updated_at`; UNIQUE(`kind`, `provider_key`)). The migration and the test schema MUST seed `password/default` (enabled, no default roles), `trusted_header/default` (enabled, `unknown_identity_role_id` = the admin preset, `no_match_role_id` = the viewer preset) and `oidc/default` (disabled, both role columns NULL) with insert-ignore semantics so an operator's later edit survives re-runs and upgrades. A provider is *active* when its row is enabled AND `dashboard_auth_mode` admits its kind: `trusted_header` only in `trusted_header` mode, `password` in every mode, `oidc` in `standard` and `trusted_header` mode but never in `disabled` mode (an install that has turned dashboard authentication off must not grow a sign-in flow that mints sessions and provisions accounts), a kind without an implementation never. The mode MUST NOT be persisted into the table. Every provider implements `kind`, `provider_key` and `resolve_identity(request) -> ExternalIdentity | None` (`provider`, `provider_key`, `subject`, `email`, `display_name`, `groups`); redirect-style providers add `begin_login`/`complete_login` and still end in the shared resolver, and a redirect-style provider's `resolve_identity` returns nothing because it recognises no one from a bare request.

#### Scenario: Fresh and upgraded installs share the seed

- **WHEN** the migration runs on an empty database or on one that already has the table
- **THEN** exactly three rows exist, the trusted-header row hands unknown identities the admin preset, the OIDC row is disabled and hands them nothing, and re-running the migration changes nothing

#### Scenario: Active follows the mode

- **GIVEN** the password and trusted-header rows are enabled
- **WHEN** `dashboard_auth_mode` is `standard`
- **THEN** only `password` is active; in `trusted_header` mode both are

#### Scenario: The OIDC row becomes active only where it should

- **GIVEN** an enabled `oidc` row
- **WHEN** `dashboard_auth_mode` is `standard` or `trusted_header`
- **THEN** it is active
- **WHEN** the mode is `disabled`
- **THEN** it is not active and its endpoints complete no sign-in

### Requirement: Provider settings API

`GET /api/auth-providers` (`security:write`) SHALL list every row as `{id, kind, providerKey, label, enabled, active, unknownIdentityRoleId, noMatchRoleId, linkByEmail, skipRoleSync, idpMfaEnforced, config, testLoginVerifiedAt, createdAt, updatedAt}` where `config` masks every secret (`****` + last four) and `active` is computed. `PATCH /api/auth-providers/{id}` (`security:write` and an attributable account, else `409 admin_account_required`) SHALL accept `label`, `enabled`, `unknownIdentityRoleId`, `noMatchRoleId` (null allowed), `linkByEmail`, `skipRoleSync`, `idpMfaEnforced` and, on an `oidc` row only, a typed `config` object; unknown fields answer `422`; a `config` body on a row of another kind answers `422 config_not_supported`; an unknown id `404 provider_not_found`; a role that is not assignable `422 role_not_assignable`; a role whose grants exceed the caller's `403 insufficient_delegation`.

Three levers on this endpoint reach further than any single role and SHALL therefore require the caller to hold **every** grant the admin preset holds, answering `403 insufficient_delegation` otherwise: writing the `config` document, setting `linkByEmail` **true**, and setting `enabled` **true** on a row whose `unknownIdentityRoleId` or `noMatchRoleId` names a role the caller could not delegate. An identity provider asserts *who someone is*, so whoever repoints the connection can point it at an identity provider they run, mint a token carrying an existing admin's subject and be resolved into that admin's account; turning e-mail linking on widens the same move from "accounts that already have an identity here" to "any active account whose address you know"; and arming a row is the same act as writing the role it already hands out, performed without ever naming it. `security:write` is permission to administer sign-in, not permission to become somebody else. The narrowing directions — clearing `linkByEmail`, disabling a row — SHALL NOT be gated, so the recovery direction stays open. Enabling a provider whose kind offers no local password fallback SHALL require at least one qualifying break-glass account and SHALL otherwise answer `409 break_glass_requires_totp` whose body names the designated account that would qualify — the same refusal, for the same reason, as tightening `local_login_policy`: an install must keep one way in that does not depend on the identity provider. Enabling an `oidc` row SHALL additionally require the acting account's own test login recorded within the last ten minutes and SHALL otherwise answer `409 oidc_test_login_required`. Disabling a provider SHALL never be gated, so the recovery direction is always open. Like the policy tightening it mirrors, the count and the provider write SHALL be one atomic step: a request that could enable a provider SHALL take the account write intent before its first read and hold it until the commit, so a concurrent account mutation cannot remove the last qualifying account in between. A change SHALL audit `provider_updated` (target `auth_provider`) with the changed fields — naming a changed secret only as the fact that it changed — and an `enabled` change SHALL additionally audit `provider_enabled` or `provider_disabled`; every change SHALL invalidate the registry on every replica.

#### Scenario: Operator changes the default role

- **WHEN** an admin patches the trusted-header row's `unknownIdentityRoleId` to the viewer preset
- **THEN** the next unknown identity becomes a viewer, earlier accounts keep their roles, and `provider_updated` names the caller

#### Scenario: Role handout is bounded by the caller's grants

- **GIVEN** a caller whose role holds `security:write` but not the admin grants
- **WHEN** it sets `unknownIdentityRoleId` to the admin preset
- **THEN** the response is `403 insufficient_delegation`, while the viewer preset succeeds

#### Scenario: Repointing the identity provider is bounded the same way

- **GIVEN** that same caller, refused the admin preset a moment ago
- **WHEN** it instead sends a `config` naming an issuer it controls, or sets `linkByEmail` true
- **THEN** each answers `403 insufficient_delegation` and the stored row is unchanged
- **AND** setting `linkByEmail` false and writing a label both succeed

#### Scenario: Arming a row that hands out admin is bounded too

- **GIVEN** a disabled row whose `unknownIdentityRoleId` is the admin preset
- **WHEN** that caller sets `enabled` true
- **THEN** the response is `403 insufficient_delegation` and the row stays disabled
- **AND** a row handing out no more than the caller holds reaches the break-glass and test-login gates as before

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

#### Scenario: The OIDC configuration is written typed and read back masked

- **WHEN** an admin patches the `oidc` row's `config` with an issuer, a client id, a client secret and claim names
- **THEN** the response and every later `GET` carry the issuer, client id, redirect URI and claim names in clear and the client secret as `****` plus its last four characters
- **AND** the same body sent to the `password` or `trusted_header` row answers `422 config_not_supported`

### Requirement: The provider settings API names the headers it reads

`GET /api/auth-providers` SHALL return, in the `config` map of a `trusted_header` row, the two header names that provider reads: `identityHeader` and `groupsHeader`, from the running settings. They are read-only facts of the deployment topology, so `PATCH /api/auth-providers/{id}` SHALL NOT accept them, and SHALL refuse a `config` body on a `trusted_header` row entirely. An `oidc` row's `config` SHALL carry that provider's connection settings, secrets masked; rows of every other kind SHALL keep an empty `config`. Without this the settings UI can only name the environment variables and not the values they carry, which is exactly the mismatch operators need to see.

#### Scenario: The reverse-proxy row carries both header names

- **WHEN** an admin lists the sign-in providers on an install with the defaults
- **THEN** the `trusted_header` row's `config` is `{"identityHeader": "Remote-User", "groupsHeader": "Remote-Groups"}` and the `password` row's `config` is empty

#### Scenario: They cannot be written

- **WHEN** a `PATCH /api/auth-providers/{id}` body carries a `config` naming either header, or any `config` at all on the `trusted_header` row
- **THEN** the request is refused — as an unknown field for the header names, because the config model forbids extras, and as `422 config_not_supported` for the row kind
