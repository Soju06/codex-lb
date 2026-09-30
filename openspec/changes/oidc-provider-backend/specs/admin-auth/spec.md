## ADDED Requirements

### Requirement: OIDC sign-in endpoints

The system SHALL serve four routes on the existing dashboard-auth router, adding no router, no CSRF exemption and no nav item:

- `GET /api/dashboard-auth/oidc/login/start` — public. It SHALL answer `404 provider_not_found` when no OIDC provider is active, and otherwise `303` to the identity provider's authorization endpoint.
- `GET /api/dashboard-auth/oidc/callback` — public. It SHALL answer `303` to an in-app path derived from the completed flow's stored purpose.
- `POST /api/dashboard-auth/oidc/test-login/start` — `security:write` and an attributable account (else `409 admin_account_required`), therefore also a recent step-up. It SHALL answer the authorization URL as JSON so the settings UI can open it, and SHALL run against the stored row whether or not it is enabled, because proving a connection is what precedes enabling it.
- `POST /api/dashboard-auth/oidc/step-up/start` — the signed-in account principal (guests and account-less principals answer `401 user_account_required`). It SHALL answer the authorization URL as JSON.

Unlike the two public routes, the two signed-in starts SHALL answer `502 oidc_provider_unreachable` when the discovery document or key set cannot be fetched, carrying no detail from the identity provider's own response: the caller is the admin who typed the URL, and telling them it does not resolve is the point of a pre-flight. Neither start is `404` for an unconfigured row alone — the test-login start SHALL answer `404 provider_not_found` when no connection document is stored, and the step-up start when no OIDC provider is active.

Both browser-facing routes SHALL be `GET` with the query response mode, so the cross-site origin middleware never inspects them: a `form_post` callback would be a cross-site `POST` that middleware refuses before routing, and the exemption that would "fix" it covers session-minting routes.

**No destination is ever accepted from the caller.** There SHALL be no `next`, `return_to` or `redirect` parameter on any of the four routes and no destination carried inside `state`; where the browser lands is derived by the server from the flow's stored purpose out of a closed set of in-app paths (the dashboard on a completed sign-in, `/auth/pending` for an identity the resolver refuses, the settings page for a completed test login or step-up, the login screen for a failure). Every redirect SHALL be a `303` to a relative path.

Failures SHALL be uniform: every callback refusal other than an unprovisioned identity SHALL end at the same failure path with no detail, SHALL NOT reflect the identity provider's `error_description`, and SHALL audit `login_failed` with `reason: oidc_login_failed` and a coarse stage name. No audit detail, log line or response SHALL carry an authorization code, a `state`, a `nonce`, an ID token, an access token or the client secret, and no log line SHALL format the request URL or its query string.

Because the server's own access logging renders the whole request line, the logging path SHALL mask the callback's `code` and `state` query parameters in every rendered record, at every level — access logs are `INFO`, below the level at which the costlier keyed redaction runs. The mask SHALL identify a parameter by its **decoded** name, because that is the name the route binds: the request line is rendered from the raw query bytes with their percent-encoding intact, while the router builds `query_params` with `parse_qsl`, which unquotes names, so `?c%6Fde=` reaches the handler as `code` and MUST be masked identically. Parameter values SHALL never be decoded, only replaced whole. The rule SHALL be anchored to the callback path, so no other route's `code` or `state` parameter is touched, and SHALL leave the rest of the query (an identity provider's `error`, a `session_state`, for instance) readable. A **refused** callback SHALL be masked exactly like a completed one: its code was never exchanged and is therefore still live at the identity provider, and refusals are what an operator reads the access log for. The unprovisioned identity is the one deliberate distinction: the person authenticated at the company identity provider, and the pending screen is what the plan asks them to see.

Both public routes SHALL be rate-limited by client address before any database work, and the callback SHALL additionally spend a per-`state` budget keyed on a hash of the value, never the value itself; a success SHALL clear only the per-`state` bucket, leaving the per-address ceiling to expire. A limited caller SHALL receive `429` with `Retry-After` as JSON rather than a redirect.

#### Scenario: The login start sends the browser to the identity provider

- **GIVEN** an active, configured OIDC provider
- **WHEN** an unauthenticated browser requests the login start
- **THEN** the response is `303` to the identity provider's authorization endpoint and sets the short-lived flow cookie
- **AND** with no active OIDC provider the response is `404 provider_not_found` and no cookie is set

#### Scenario: No caller-supplied destination exists

- **WHEN** the start or the callback is requested with `next`, `return_to` or `redirect` query parameters naming an external site
- **THEN** every parameter is ignored, and every redirect the flow issues is a relative in-app path

#### Scenario: Every failure looks the same

- **WHEN** callbacks arrive with an unknown state, a state whose cookie is missing, a mismatched nonce, or a token exchange the identity provider refuses
- **THEN** all four answer the same `303` to the same failure path with no detail in the response
- **AND** each writes one `login_failed` row with `reason: oidc_login_failed` whose details carry no code, state, nonce or token

#### Scenario: The callback is a safe method and needs no exemption

- **WHEN** the cross-site origin middleware evaluates the route table
- **THEN** the callback and the login start are safe methods it does not inspect, the two starts are same-origin dashboard `POST`s it does protect, and the exemption list is unchanged

#### Scenario: The callback's credentials never reach the access log

- **WHEN** a callback carrying `code` and `state` is served, whether it completes, is refused, or is rate-limited
- **THEN** the rendered access line carries neither value, the path and status stay readable, and an `error` parameter from the identity provider is still shown
- **AND** the same query parameters on any other route are left alone

#### Scenario: An encoded parameter name is still the parameter

- **WHEN** the callback is served with the credentials under percent-encoded names (`?c%6Fde=…&%73tate=…`), which the router decodes to `code` and `state`
- **THEN** the rendered access line carries neither value
- **AND** a name that merely contains those letters (`session_state`, `decode`, `statement`) is left readable

#### Scenario: A captured code cannot be hammered

- **WHEN** a caller replays one callback URL repeatedly
- **THEN** the per-address and per-state budgets answer `429` with `Retry-After` as JSON, and the stored state is not revealed by the key

### Requirement: The OIDC login flow is single-use, browser-bound and exchanged exactly once

Starting a flow SHALL generate a `state`, a `nonce` and a PKCE `code_verifier` from a cryptographic source, send `code_challenge_method=S256` and the S256 challenge (`plain` SHALL never be sent or accepted), and persist a row in `dashboard_oidc_login_flows` holding the SHA-256 of the `state` as its primary key, the SHA-256 of the `nonce`, the `code_verifier` encrypted with the existing `TokenEncryptor`, the purpose, the acting account for a test or step-up flow, the redirect URI as sent, a digest of the connection document the flow was started against, and a ten-minute expiry. The clear `state` and `nonce` SHALL NOT be stored. The same start SHALL set the cookie `codex_lb_oidc_flow` carrying the `state` sealed with the same encryptor, `HttpOnly`, `SameSite=Lax` (not `Strict`, which a top-level cross-site callback navigation would drop), `Secure` on HTTPS, scoped to the OIDC path, with a ten-minute max-age.

The callback SHALL further require the provider to still hold the connection document the flow named: a flow whose provider was reconfigured while the browser was at the identity provider SHALL end in the uniform failure **before** the authorization code is exchanged. A code issued by one identity provider is not exchangeable at another, and a pre-flight proves the connection it reached rather than whichever one the row holds when the browser returns.

The callback SHALL require **both**: the `state` in the query must equal the `state` the cookie carries, compared in constant time, and the row must still exist. The row SHALL be consumed by one conditional `DELETE … RETURNING` so that exactly one caller in the fleet wins it, and that consume SHALL happen **before** the authorization code is exchanged, so a replayed code can never reach the token endpoint twice through this system. The token request SHALL be issued once, on the non-retrying client, over `https`, with the client secret sent in the request rather than a URL, and its body SHALL NOT be logged. The `nonce` claim of the returned ID token SHALL be required to match the row's stored hash in constant time; because the row is already gone, a `nonce` is accepted at most once. An expired row SHALL be treated as absent, and expired rows SHALL be purged opportunistically on the next start and by the periodic cleanup leader pass. The flow cookie SHALL be cleared with the same attributes when the callback completes, however it completes.

#### Scenario: PKCE is S256

- **WHEN** a flow starts
- **THEN** the authorization request carries `code_challenge_method=S256` and the challenge derived from the verifier, and no code path can send or accept `plain`

#### Scenario: A reused state is refused

- **GIVEN** a callback that has already completed
- **WHEN** the same callback URL is requested again with the same cookie still present
- **THEN** the row is gone, the request ends in the uniform failure, and no token exchange is attempted

#### Scenario: A replayed authorization code never reaches the token endpoint twice

- **GIVEN** two callbacks carrying the same code and state arriving simultaneously on two replicas
- **WHEN** both attempt to consume the flow
- **THEN** exactly one wins the conditional delete and exchanges the code, and the other ends in the uniform failure without contacting the identity provider

#### Scenario: A state without its browser is refused

- **GIVEN** a valid, unconsumed flow started in one browser
- **WHEN** the callback URL is opened in another browser, or with the flow cookie stripped, or with a cookie carrying a different state
- **THEN** each ends in the uniform failure and the flow row is left unconsumed for its owner

#### Scenario: A mismatched nonce is refused

- **GIVEN** a valid state and a successful code exchange
- **WHEN** the returned ID token carries a nonce that is not the one this flow sent, or carries none at all
- **THEN** the sign-in is refused, no session is issued, and no account is created or linked

#### Scenario: An abandoned flow expires

- **GIVEN** a flow started eleven minutes ago
- **WHEN** its callback finally arrives
- **THEN** the row is treated as absent, the request ends in the uniform failure, and the row is purged

### Requirement: An OIDC session is an account session, capped, and is not a local password session

A completed OIDC sign-in SHALL issue the same version-2 account session cookie every other account sign-in issues, with `auth_method` `oidc`, and SHALL audit `login_success` with that method. Its lifetime SHALL be capped at the existing twelve-hour remote-session ceiling; a shorter configured dashboard lifetime SHALL still win. No new setting and no new constant SHALL be introduced for the cap.

The session's `password_verified` flag SHALL be set, because it is the flag the session gate reads to admit a cookie at all — and for that exact reason the two places that treat that flag as evidence of a *local password session* when applying `local_login_policy` (the password-fallback principal and the header-less fallback branch) SHALL additionally require `auth_method` to be `password`. Without that, an OIDC session would count as the local fallback and single sign-on would silently defeat `admins_only` and `break_glass_only`, the two policies that exist to close the local door once single sign-on is on.

#### Scenario: The SSO session is capped at twelve hours

- **GIVEN** a configured dashboard session lifetime of one year
- **WHEN** an account completes OIDC sign-in
- **THEN** the cookie and its sealed payload both expire in twelve hours
- **AND** a configured lifetime of one hour yields one hour

#### Scenario: An OIDC session is not a local password fallback

- **GIVEN** `local_login_policy` is `admins_only` or `break_glass_only`
- **WHEN** an account holding neither the admin preset nor the break-glass designation signs in through OIDC and then sends a request in `trusted_header` mode with no identity header
- **THEN** it is not admitted as a password fallback, and the session response does not report an active password session
- **AND** an actual local password session of an admitted account still is

#### Scenario: The session is described like any other

- **WHEN** an account that signed in through OIDC requests the session
- **THEN** `auth_method` is `oidc`, `user` names the account, and its permissions come from its role exactly as for a password session

### Requirement: Re-authentication at the identity provider is a step-up factor for an account that has no other

An account that holds **neither** a password hash **nor** a TOTP secret and has an identity on an active OIDC provider SHALL be able to step up by re-authenticating at that provider, and its step-up methods SHALL be reported as `["oidc"]`. An account that holds a password hash or a TOTP secret SHALL be unaffected: its methods, and what it must present, are unchanged. Offering an identity-provider round trip as an *alternative* to a factor the account holds would let a stolen session cookie plus a still-live provider session change security settings while presenting nothing the attacker does not already have.

`POST /api/dashboard-auth/oidc/step-up/start` SHALL begin a flow whose authorization request carries `prompt=login` and `max_age=0`. On completion the system SHALL require that the verified identity's `(provider, provider_key, subject)` is already linked to the acting account, and that the ID token carries an `auth_time` claim no older than the step-up window (with the same clock-skew tolerance); a missing `auth_time` after `max_age` was requested SHALL be refused rather than treated as success. It SHALL then record the step-up exactly as the step-up endpoint does — re-issuing the session cookie's `su` for a cookie session and setting the generation-bound step-up cookie otherwise — and audit `step_up_verified` with `details.methods` `["oidc"]`. No account is created, linked or re-evaluated by this flow.

The dashboard's step-up dialog SHALL be able to drive that factor. When the interrupted request reports `details.methods` `["oidc"]` the dialog SHALL render no password and no code input and SHALL NOT offer the body submit — an empty `/step-up` payload is refused, which is the dead end `step_up_unavailable` was introduced to remove — and SHALL instead offer one action that calls `POST /api/dashboard-auth/oidc/step-up/start` and follows the returned authorization URL. Because that is a full page navigation, the interrupted request SHALL be settled as *not verified* before leaving, rather than left pending: nothing can replay it across a page load, and the person repeats the action after the callback returns them to the app. A payload that ever listed `oidc` alongside `password` or `totp` SHALL still ask for those stronger factors.

#### Scenario: The dialog drives the identity-provider round trip

- **GIVEN** a sensitive mutation refused `403 step_up_required` with `details.methods` `["oidc"]`
- **WHEN** the step-up dialog opens
- **THEN** it shows neither a password field, nor a code field, nor the body submit
- **AND** its one action starts the OIDC step-up flow and navigates to the authorization URL the server returned
- **AND** the interrupted request is settled as not verified rather than left pending across the navigation

#### Scenario: An OIDC-only admin can finally step up

- **GIVEN** an account with no password and no TOTP secret whose identity is on an active OIDC provider
- **WHEN** it is refused `403 step_up_unavailable` today and instead completes the OIDC step-up flow
- **THEN** the refusal becomes `403 step_up_required` with `details.methods` `["oidc"]` before the flow and the sensitive mutation succeeds after it
- **AND** `step_up_verified` records `methods` `["oidc"]`

#### Scenario: An account that holds a factor is not offered the shortcut

- **GIVEN** an account holding a password hash, or a TOTP secret, or both
- **WHEN** its step-up methods are computed
- **THEN** they are exactly what they were before this change, and completing an OIDC step-up flow does not satisfy step-up for it

#### Scenario: A stale re-authentication is refused

- **GIVEN** a step-up flow whose identity provider returned an ID token with no `auth_time`, or an `auth_time` older than the step-up window
- **WHEN** the callback completes
- **THEN** no step-up is recorded, the sensitive mutation still answers `403`, and the failure is audited

#### Scenario: Another person's identity cannot step up for this account

- **GIVEN** a step-up flow started by one account
- **WHEN** the person authenticates at the identity provider as a different subject
- **THEN** no step-up is recorded for either account and the flow ends in the uniform failure

## MODIFIED Requirements

### Requirement: Sensitive mutations require a recent step-up

Every non-safe request (`POST`, `PUT`, `PATCH`, `DELETE`) authorised by `security:write`, `users:manage`, `roles:manage` or `accounts:export` (the `STEP_UP_PERMISSIONS` set) SHALL additionally require that the acting account re-verified a credential within the last 300 seconds. `PUT /api/settings` SHALL require it exactly when the request changes the stored value of a security field (the existing changed-field rule). Reads SHALL never require it. Principals without an account (the implicit local admin, the disabled-auth principal) have nothing to re-verify and SHALL NOT be gated; every account principal SHALL be, whatever provider signed it in, and a provider's `idp_mfa_enforced` flag SHALL NOT waive it. The step-up is recorded by the `su` claim of the session cookie, by the step-up cookie for the same account, or by a password session of the same account accompanying a trusted-header request. When none is fresh the request SHALL answer `403 step_up_required` with `param` naming the permission and `details.methods` listing the factors the account must present (`password` when it holds a password hash, `totp` when it holds a TOTP secret, both when it holds both, and `oidc` when it holds neither but has an identity on an active OIDC provider); when the account has no factor at all the request SHALL answer `403 step_up_unavailable` with the message "Set up two-factor authentication or a local password to change security settings".

#### Scenario: Stale session is asked to confirm

- **GIVEN** a password account whose last step-up is older than 300 seconds
- **WHEN** it sends `PUT /api/settings` changing `guest_access_enabled`
- **THEN** the response is `403 step_up_required` with `param` `security:write` and `details.methods` `["password"]`
- **AND** `GET /api/settings` and a `PUT /api/settings` that changes only non-security fields succeed

#### Scenario: Reverse-proxy account without any factor

- **GIVEN** a trusted-header account with no password, no TOTP secret and no OIDC identity
- **WHEN** it sends `PATCH /api/auth-providers/{id}`
- **THEN** the response is `403 step_up_unavailable`
- **AND** `GET /api/auth-providers` still succeeds

#### Scenario: Passwordless local install is not asked

- **WHEN** the implicit local admin sends `POST /api/dashboard-auth/guest/password`
- **THEN** the request succeeds without a step-up

#### Scenario: An account whose only factor is its identity provider

- **GIVEN** an account with no password and no TOTP secret whose identity is on an active OIDC provider
- **WHEN** it sends a mutation gated by `security:write`
- **THEN** the response is `403 step_up_required` with `details.methods` `["oidc"]`, not `403 step_up_unavailable`

### Requirement: Step-up endpoint and cookies

`POST /api/dashboard-auth/step-up` `{password?, code?}` SHALL re-verify the signed-in account principal (session cookie or trusted-header identity; guests and account-less principals answer `401 user_account_required`). The account MUST present every factor it holds: its password when it has a hash, its TOTP code when it has a secret (the replay counter advances). Any refusal SHALL answer `401 invalid_credentials` with an identical body; attempts SHALL spend the per-client password limiter budget (8 per 60 s, `429 step_up_rate_limited`); an account whose only factor is an identity provider SHALL answer `403 step_up_unavailable` here, because this endpoint takes a body and that factor takes a redirect round trip, and an account with no factor at all SHALL answer the same. Success SHALL audit `step_up_verified` (actor = the account, `details.methods`) and answer `{verifiedAt, expiresAt = verifiedAt + 300}`. For a password session it SHALL re-issue the session cookie with `su = now`, keeping the method, TOTP step and remaining lifetime; for a trusted-header account it SHALL set the cookie `codex_lb_step_up` (sealed `{v: 1, uid, su, exp = su + 300}`, HttpOnly, SameSite=Lax, Secure on HTTPS, five-minute max-age), which SHALL be honoured only when its `uid` is the acting account. Password login and invite acceptance SHALL mint `su = now` when the account has no TOTP secret; `/totp/verify` SHALL mint `su = now` **only when it completes a recent local password sign-in** — a session whose `auth_method` is `password` — because that is the case in which both of the account's factors were presented inside the window. Completing it against a session an identity provider minted SHALL NOT mint a step-up, however recent that session is: its `password_verified` flag is set because that is the flag the session gate reads, not because a password was presented, and stamping there would let an account that holds a password change every security setting having proven one of its two required factors. A password change SHALL copy `su` unchanged into the re-issued cookie. A completed OIDC step-up flow SHALL mint the step-up through these same two paths and SHALL NOT introduce a third.

#### Scenario: Password account confirms and the change goes through

- **GIVEN** a password account without TOTP that was refused with `step_up_required`
- **WHEN** it posts `{password}` to `/step-up` and repeats the change
- **THEN** `/step-up` answers `200` with `verifiedAt` and `expiresAt`, the session cookie now carries `su`, the change succeeds, and a `step_up_verified` audit row names the account

#### Scenario: Password account with TOTP presents both

- **GIVEN** a password account with a TOTP secret
- **WHEN** it posts only `{password}` or only `{code}`
- **THEN** the response is `401 invalid_credentials`
- **AND** posting both succeeds and the same code is refused afterwards

#### Scenario: Reverse-proxy account confirms by code

- **GIVEN** a trusted-header account that enrolled TOTP and has no password
- **WHEN** it posts `{code}` to `/step-up`
- **THEN** the response sets `codex_lb_step_up`, the sensitive route succeeds for that account and stays refused for another identity presenting the same cookie
- **AND** 301 seconds later the route answers `403 step_up_required` again

#### Scenario: A second factor on an SSO session is not a password

- **GIVEN** an account holding a password hash that signed in through OIDC and then enrolled and verified a TOTP secret
- **WHEN** it repeats a mutation gated by `security:write`
- **THEN** the response is still `403 step_up_required` with `details.methods` `["password", "totp"]` and the session carries no `su`
- **AND** the same `/totp/verify` completing a fresh local password sign-in still mints one

#### Scenario: The body endpoint cannot serve a redirect factor

- **GIVEN** an account whose only step-up method is `oidc`
- **WHEN** it posts anything to `/step-up`
- **THEN** the response is `403 step_up_unavailable`
- **AND** completing the OIDC step-up flow instead sets the same cookie the code path would have set

### Requirement: Session response describes the account and the login screen

`GET /api/dashboard-auth/session` (and the session-issuing endpoints) SHALL add the optional fields `user` (`{id, username, display_name, role: {id, slug, name, kind}}` or null), `auth_method`, `must_change_password`, `totp_enrollment_required`, `login`, `access_summary`, `assignable_role_ids`, and `step_up`. `login` MUST be present for every caller, MUST never contain a username, MUST set `username_field` to `hidden` exactly when one active account holds a password and to `shown` otherwise, MUST list the active providers in `providers` (`{kind, provider_key, label, login_url}`; `password` first, `trusted_header` when active, `oidc` when active; `login_url` is the provider's own sign-in start path for a redirect-style provider and null for the others), MUST set `pending_identity` to true exactly when the request carried a provider identity that resolved to no active account, and MUST report `local_login` as the effective `local_login_policy` (`enabled`, `admins_only` or `break_glass_only`) so the login screen knows whether the local password form is shown, collapsed behind a link, or reachable only at `/login?local=1`. `login` MUST NOT name the break-glass account or any other username to an unauthenticated caller; the account name is served only to the authenticated `security:write` caller who can act on it. `access_summary` (`users_total`, `users_active`, `users_invited`, `users_disabled`, `pending_invites`, `non_admin_users`, `custom_roles`, `providers_enabled` = the active provider kinds, `role_mappings`, `scim_tokens`, `audit_sinks`, `local_login_policy`) and a non-empty `assignable_role_ids` MUST be returned only to an authenticated principal holding `users:manage`; every other caller MUST receive `null` and `[]`. `assignable_role_ids` MUST list exactly the `admin`, `operator`, and `viewer` presets in this release (`member` becomes assignable when own-scoped views ship; `guest` never). `step_up` (`{verified_at, expires_at, methods}`) MUST be returned for signed-in accounts only: `verified_at`/`expires_at` are set when a step-up recorded for the account is still within 300 seconds (from the session cookie's `su` or the step-up cookie) and null otherwise, and `methods` lists the factors the account must present (`password`, `totp`, both, `oidc`, or none); every other caller MUST receive `null`. `break_glass_session` MUST be true exactly when the session was minted for an account carrying the break-glass designation and false otherwise (never null for a signed-in account), so the header can show the emergency-session indicator. `permissions` MUST keep the `read`/`write` aliases and additionally list every grant as `<permission>:<scope>`. `role` MUST remain `admin` or `guest`. `password_required` MUST equal the derived "sign-in required" state, except that a header-less request in `trusted_header` mode while no active account holds a password reports `password_required=false` and `authenticated=false` (the client shows the reverse-proxy notice, not a login form). A trusted-header request resolved to an account SHALL be described like that account's session (`authenticated=true`, `user`, `auth_method=trusted_header`, the account's permissions, team facts when it holds `users:manage`); `password_session_active` reports whether a fallback password cookie the login policy admits also rode along, which an OIDC session is not.

#### Scenario: Signed-in admin sees the account block and the summary

- **WHEN** an admin account requests the session
- **THEN** `user.username` is the account's username, `auth_method` is `password`, `login.username_field` is `hidden`
- **AND** `access_summary.users_total` counts every account and `permissions` contains `read`, `write`, and `users:manage:all`

#### Scenario: Guest and unauthenticated callers get no team facts

- **WHEN** a guest session or an unauthenticated client requests the session
- **THEN** `access_summary` is null, `assignable_role_ids` is empty, `user` is null and `step_up` is null
- **AND** `login` is present without any username

#### Scenario: Refused proxy identity

- **WHEN** a trusted-header request whose identity resolves to no active account asks for the session
- **THEN** `authenticated` is false, `user` is null and `login.pending_identity` is true

#### Scenario: Step-up block follows the account

- **GIVEN** a password account without TOTP that signed in 301 seconds ago and has not stepped up since
- **WHEN** it requests the session
- **THEN** `step_up` is `{verified_at: null, expires_at: null, methods: ["password"]}`
- **AND** after a successful `/step-up` the block carries `verified_at` and `expires_at = verified_at + 300`

#### Scenario: Unauthenticated caller learns the policy but no name

- **GIVEN** `local_login_policy` is `break_glass_only`
- **WHEN** an unauthenticated client requests the session
- **THEN** `login.local_login` is `break_glass_only` and `login.providers` lists the active providers
- **AND** the response contains no username and `access_summary` is null

#### Scenario: Emergency session is reported

- **WHEN** a break-glass admin that completed both factors requests the session
- **THEN** `break_glass_session` is true
- **AND** the same field is false for every other signed-in account

#### Scenario: An active OIDC provider is advertised with its start path

- **GIVEN** an enabled and active OIDC row labelled by the operator
- **WHEN** any caller requests the session
- **THEN** `login.providers` contains an entry of kind `oidc` carrying that label and a `login_url` of the OIDC sign-in start path
- **AND** the entry disappears when the row is disabled, and the client that does not know the kind ignores it
