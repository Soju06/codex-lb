## MODIFIED Requirements

### Requirement: OIDC sign-in endpoints

The system SHALL serve four routes on the existing dashboard-auth router, adding no router, no CSRF exemption and no nav item:

- `GET /api/dashboard-auth/oidc/login/start` — public. It SHALL answer `404 provider_not_found` when no OIDC provider is active, and otherwise `303` to the identity provider's authorization endpoint.
- `GET /api/dashboard-auth/oidc/callback` — public. It SHALL answer `303` to an in-app path derived from the completed flow's stored purpose.
- `POST /api/dashboard-auth/oidc/test-login/start` — `security:write` and an attributable account (else `409 admin_account_required`), therefore also a recent step-up. It SHALL answer the authorization URL as JSON so the settings UI can open it, and SHALL run against the stored row whether or not it is enabled, because proving a connection is what precedes enabling it.
- `POST /api/dashboard-auth/oidc/step-up/start` — the signed-in account principal (guests and account-less principals answer `401 user_account_required`). It SHALL answer the authorization URL as JSON.

Unlike the two public routes, the two signed-in starts SHALL answer `502 oidc_provider_unreachable` when the discovery document or key set cannot be fetched, carrying no detail from the identity provider's own response: the caller is the admin who typed the URL, and telling them it does not resolve is the point of a pre-flight. Neither start is `404` for an unconfigured row alone — the test-login start SHALL answer `404 provider_not_found` when no connection document is stored, and the step-up start when no OIDC provider is active.

Both browser-facing routes SHALL be `GET` with the query response mode, so the cross-site origin middleware never inspects them: a `form_post` callback would be a cross-site `POST` that middleware refuses before routing, and the exemption that would "fix" it covers session-minting routes.

**No destination is ever accepted from the caller.** There SHALL be no `next`, `return_to` or `redirect` parameter on any of the four routes and no destination carried inside `state`; where the browser lands is derived by the server from the flow's stored purpose out of a closed set of in-app paths (the dashboard on a completed sign-in, `/auth/pending` for an identity the resolver refuses, the settings page for a completed test login or step-up, the login screen for a failure). Every redirect SHALL be a `303` to a relative path.

**The refused identity is the one destination that carries anything back.** The browser that lands on the pending screen holds no session and no state, and the person behind it has just authenticated at the company identity provider, so the refusal redirect SHALL additionally set one short-lived sealed marker cookie — the same seal as the flow cookie, `HttpOnly`, `SameSite=Lax`, `Secure` on HTTPS, a ten-minute max-age, scoped to the dashboard-auth path — carrying the provider row's id and the **masked** form of the e-mail the identity provider asserted (the first character of the local part, then `***`, then the domain), and nothing else: no subject, no groups, no address in clear, no token, no claim. An identity provider that asserted no address SHALL leave no marker at all rather than a marker with an empty reference — there would be nothing for the person to quote and the refusal row it would be matched against carries no address either. Every other destination — a completed sign-in, a completed pre-flight, a completed re-authentication and every failure — SHALL clear that cookie with the same attributes, and signing out SHALL clear it too, so it exists only for the browser it was written for and only while it is useful. It SHALL NOT be single-use, because the pending screen's **Try again** re-reads the session; nothing but the session response SHALL read it.

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

#### Scenario: A refused identity is handed back masked, to the browser it belongs to

- **WHEN** the callback resolves an identity the resolver refuses
- **THEN** the `303` to the pending path sets the sealed marker cookie carrying the provider row's id and the masked address, and no response body, redirect URL or log line carries the address in clear
- **AND** a completed sign-in, a completed pre-flight, a completed re-authentication and every failure destination clear that cookie rather than setting it, as does signing out
- **AND** the same refusal for an identity carrying no e-mail sets no marker, and the session response that follows reports `pending_arrival` null

### Requirement: Session response describes the account and the login screen

`GET /api/dashboard-auth/session` (and the session-issuing endpoints) SHALL add the optional fields `user` (`{id, username, display_name, role: {id, slug, name, kind}}` or null), `auth_method`, `must_change_password`, `totp_enrollment_required`, `login`, `access_summary`, `assignable_role_ids`, and `step_up`. `login` MUST be present for every caller, MUST never contain a username, MUST set `username_field` to `hidden` exactly when one active account holds a password and to `shown` otherwise, MUST list the active providers in `providers` (`{kind, provider_key, label, login_url}`; `password` first, `trusted_header` when active, `oidc` when active; `login_url` is the provider's own sign-in start path for a redirect-style provider and null for the others), MUST set `pending_identity` to true exactly when the request carried a provider identity that resolved to no active account **or** carries the sealed refusal marker an OIDC callback left for this browser, MUST carry `pending_arrival` (`{provider, reference}`) exactly when that marker is present and readable — `provider` being the label of the row the marker names and `reference` the masked address it carries — and `null` otherwise, so a reverse-proxy refusal keeps the bare boolean it has today, and MUST report `local_login` as the effective `local_login_policy` (`enabled`, `admins_only` or `break_glass_only`) so the login screen knows whether the local password form is shown, collapsed behind a link, or reachable only at `/login?local=1`. `login` MUST NOT name the break-glass account or any other username to an unauthenticated caller; the account name is served only to the authenticated `security:write` caller who can act on it. `pending_arrival` MUST be derived from that marker and from nothing else — not from the active providers, not from any account lookup — so no caller can obtain the block for an address of their choosing, and what it returns is a lossy projection of an identity this browser itself presented, never a statement that an account exists. `access_summary` (`users_total`, `users_active`, `users_invited`, `users_disabled`, `pending_invites`, `non_admin_users`, `custom_roles`, `providers_enabled` = the active provider kinds, `role_mappings`, `scim_tokens`, `audit_sinks`, `local_login_policy`) and a non-empty `assignable_role_ids` MUST be returned only to an authenticated principal holding `users:manage`; every other caller MUST receive `null` and `[]`. `assignable_role_ids` MUST list exactly the `admin`, `operator`, and `viewer` presets in this release (`member` becomes assignable when own-scoped views ship; `guest` never). `step_up` (`{verified_at, expires_at, methods}`) MUST be returned for signed-in accounts only: `verified_at`/`expires_at` are set when a step-up recorded for the account is still within 300 seconds (from the session cookie's `su` or the step-up cookie) and null otherwise, and `methods` lists the factors the account must present (`password`, `totp`, both, `oidc`, or none); every other caller MUST receive `null`. `break_glass_session` MUST be true exactly when the session was minted for an account carrying the break-glass designation and false otherwise (never null for a signed-in account), so the header can show the emergency-session indicator. `permissions` MUST keep the `read`/`write` aliases and additionally list every grant as `<permission>:<scope>`. `role` MUST remain `admin` or `guest`. `password_required` MUST equal the derived "sign-in required" state, except that a header-less request in `trusted_header` mode while no active account holds a password reports `password_required=false` and `authenticated=false` (the client shows the reverse-proxy notice, not a login form). A trusted-header request resolved to an account SHALL be described like that account's session (`authenticated=true`, `user`, `auth_method=trusted_header`, the account's permissions, team facts when it holds `users:manage`); `password_session_active` reports whether a fallback password cookie the login policy admits also rode along, which an OIDC session is not.

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

#### Scenario: A refused company sign-in describes itself to the browser that was refused

- **GIVEN** a browser carrying the sealed refusal marker an OIDC callback left for it
- **WHEN** it requests the session
- **THEN** `authenticated` is false, `user` is null, `login.pending_identity` is true and `login.pending_arrival` carries the provider row's label with the masked address
- **AND** the same request without that cookie reports `pending_identity` false and `pending_arrival` null, and neither answer says whether any account exists

#### Scenario: An active OIDC provider is advertised with its start path

- **GIVEN** an enabled and active OIDC row labelled by the operator
- **WHEN** any caller requests the session
- **THEN** `login.providers` contains an entry of kind `oidc` carrying that label and a `login_url` of the OIDC sign-in start path
- **AND** the entry disappears when the row is disabled, and the client that does not know the kind ignores it
