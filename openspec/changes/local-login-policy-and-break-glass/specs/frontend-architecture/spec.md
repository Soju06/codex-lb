## ADDED Requirements

### Requirement: The local password form follows the login policy

The login screen SHALL read `login.localLogin` and render the local password form accordingly. Under `enabled` it renders as it does today. Under `admins_only` it SHALL be collapsed behind a link ("Sign in with a password") that reveals it in place, because the form still works for some accounts and hiding it entirely would strand them. Under `break_glass_only` it SHALL NOT render at all on `/`, and SHALL render only when the URL carries `local=1`; the screen SHALL show the active providers from `login.providers` in either case. No surface SHALL ever put the break-glass username in front of an unauthenticated client — not as a prefill, not as help text — and the remembered-username prefill SHALL keep working from `localStorage` alone, which is the person's own browser and not a server disclosure. Every surface that can render the local form — the login screen and the pending-identity screen — SHALL apply the same rule from the same value.

#### Scenario: Collapsed under admins_only

- **GIVEN** `login.localLogin` is `admins_only`
- **WHEN** the login screen renders
- **THEN** the password field is not visible until the person activates the link that reveals it

#### Scenario: Hidden under break_glass_only

- **GIVEN** `login.localLogin` is `break_glass_only`
- **WHEN** the login screen renders at `/`
- **THEN** no password field and no reveal link are rendered, only the active providers
- **AND** the same screen at `/login?local=1` renders the password field

#### Scenario: The emergency account is never named

- **WHEN** an unauthenticated client renders `/login?local=1`
- **THEN** the username field is empty unless the browser itself remembered a name, and no copy names the break-glass account

### Requirement: Login policy card

The Organisation group SHALL contain a login-policy card (`id="organisation-login-policy"`) for sessions holding `security:write`. It SHALL show the current `local_login_policy` with a control to change it, the qualifying state in plain words, and, when nothing qualifies, the hint that names the account to fix ("Turn on two-factor for `<username>` to qualify") rather than a bare error. It SHALL show the `/login?local=1` URL and the break-glass account name together, as facts to copy into a password manager, and SHALL make both selectable. A refusal from the server SHALL render as explained copy: `break_glass_requires_totp` and `last_break_glass_protected` SHALL be added to the organisation feature's explained error codes so the raw server message never surfaces, and the same two codes SHALL be explained in the People tab and on the self-service two-factor card, which are the other places the guard can refuse. A successful write SHALL refresh the session so the derived disclosure tier, which already treats a non-default policy as enterprise, updates in the same interaction.

#### Scenario: Nothing qualifies yet

- **GIVEN** the designated `admin` account has no second factor
- **WHEN** an admin expands the Organisation group
- **THEN** the card says the policy cannot be tightened yet and names `admin` as the account to enrol

#### Scenario: The operator can save the emergency way in

- **GIVEN** a qualifying break-glass account
- **WHEN** the card renders
- **THEN** it shows the `/login?local=1` URL and the account's username

#### Scenario: A refusal is explained, not echoed

- **WHEN** the policy change answers `409 break_glass_requires_totp`
- **THEN** the card renders the explained sentence and not the raw server message

#### Scenario: Tightening updates the rest of the page

- **WHEN** the policy is changed successfully
- **THEN** the session is refreshed and the collapsed group line becomes the status summary

### Requirement: Emergency session indicator

When the session reports `breakGlassSession`, the header SHALL render an "Emergency session" indicator in its actions row. It SHALL be a sibling of the account chip rather than an item inside it, because a break-glass session can exist on an install the store still derives as the individual tier, where no chip is rendered at all; the mobile navigation sheet SHALL carry the same indicator. The flag SHALL be part of the store's least-privilege reset, so signing out or being signed out clears it and no indicator can survive into the next session.

#### Scenario: The indicator follows the session

- **GIVEN** a session reporting `breakGlassSession`
- **WHEN** the header renders on the individual tier and on the team tier
- **THEN** the indicator is visible in both, and in the mobile sheet

#### Scenario: The indicator does not outlive the session

- **WHEN** the person signs out, or a request is answered `401`
- **THEN** the store's reset clears the flag and the next unauthenticated render shows no indicator

## MODIFIED Requirements

### Requirement: Public routes render before the login branch

The auth gate SHALL read the current location and SHALL render public routes before its login branch, so that a public route renders for a visitor without a session and for a signed-in principal alike and is never replaced by the login form, the TOTP dialog or the bootstrap screen. The public routes in this release are `/invite/:token`, which SHALL render the invite acceptance screen keyed by the token; `/auth/pending`, which SHALL render the pending-identity screen; and `/login`, which SHALL render the login screen and, with `?local=1`, the local password form even when the policy would otherwise hide it. Every public route SHALL also be reachable through the router for an authenticated session — `/login` SHALL redirect a signed-in session to the dashboard, exactly as `/auth/pending` does — so no public URL can dead-end on the not-found page. Whether a URL carries `local=1` SHALL be decided by a pure helper over `location.search` with its own unit test, not by a query hook inside the login form, so the form keeps rendering without a router in tests. All other paths SHALL keep the existing gate behaviour.

#### Scenario: Visitor opens an invite link

- **GIVEN** the session response says sign-in is required and the client has no session
- **WHEN** the app loads at `/invite/<token>`
- **THEN** the invite acceptance screen renders
- **AND** the login form is not rendered

#### Scenario: Signed-in account opens an invite link

- **GIVEN** an authenticated account session
- **WHEN** the app loads at `/invite/<token>`
- **THEN** the invite acceptance screen renders instead of the app
- **AND** it tells the person which account is signed in and offers a Logout button

#### Scenario: Break-glass URL renders the form

- **GIVEN** `local_login.policy` is `break_glass_only` and the client has no session
- **WHEN** the app loads at `/login?local=1`
- **THEN** the local password form renders

#### Scenario: Break-glass URL does not dead-end for a signed-in session

- **GIVEN** an authenticated session
- **WHEN** the app loads at `/login?local=1`
- **THEN** the router sends it to the dashboard and the not-found page is not rendered

### Requirement: Pending identity screen

`/auth/pending` SHALL be a public route rendered by `AuthGate` before the login branch. It SHALL show "Your account is not ready yet", explain that the proxy recognised the person but the dashboard has no account for them, and offer **Try again** (refreshes the session) and **Logout**; when `login.providers` lists `password`, `localPasswordConfigured` is true and `login.localLogin` is not `break_glass_only` it SHALL also render the local login form so the break-glass admin can sign in behind the proxy; under `break_glass_only` it SHALL instead render the same link to `/login?local=1` the login screen uses, so the policy has exactly one door and the pending screen cannot become a second one. `AuthGate` SHALL render the same screen — and navigate to `/auth/pending` — whenever the session reports `login.pendingIdentity` for an unauthenticated caller, except on other public routes (`/invite/:token`, `/login…`), which keep their URL. The URL alone renders the pending screen only on a `trusted_header` install. The router SHALL map `/auth/pending` to the dashboard for an authenticated session so a retry after provisioning lands in the app.

#### Scenario: Refused proxy identity

- **WHEN** the session response carries `login.pendingIdentity: true` and `authenticated: false`
- **THEN** the pending screen renders instead of the login form and Try again refreshes the session
- **AND** with `localPasswordConfigured` true the local login form renders beneath it

#### Scenario: Public routes are not redirected

- **WHEN** a pending-identity session opens `/invite/abc123`
- **THEN** the invite screen renders and the URL is unchanged

#### Scenario: Signed in on the pending route

- **WHEN** an authenticated session opens `/auth/pending`
- **THEN** the router redirects to `/dashboard`

#### Scenario: The pending screen is not a second door

- **GIVEN** `login.localLogin` is `break_glass_only` and a local password is configured
- **WHEN** the pending screen renders
- **THEN** no password field is rendered and a link to `/login?local=1` is offered instead

### Requirement: Organisation settings group

The Settings page SHALL render a collapsed **Organisation** group (`id="organisation"`) after the Advanced settings group, reusing the Advanced group's component so that collapsing genuinely unmounts its children. It SHALL render for every session holding `security:write` — this is where a single-person install first meets the company-login machinery, so the one line is the discovery surface and is drawn before anything is configured. Visibility SHALL be derived from the permission alone, a fact the session already carries: it SHALL NOT depend on `accessSummary`, which is absent for a principal without `users:manage`, and SHALL NOT depend on any request, because none may be issued while the group is collapsed.

While nothing is configured the collapsed line SHALL read as one plain sentence about what the group is for — "Organisation — company login, automatic account management, audit export" — and SHALL NOT contain the words user, role, SSO, SCIM, IdP or RBAC, in any casing. Once `accessSummary` reports something configured (a non-password provider, role mappings, custom roles, SCIM tokens, audit sinks, or a tightened local login policy) the same line SHALL become a status summary of what is on; the summary SHALL name features, not role names, and SHALL obey the same word ban. Expanding SHALL take one interaction and SHALL mount the child cards in order: reverse-proxy card, then group-to-role rules card, then login-policy card. The first two describe a reverse-proxy row and SHALL render only when one exists; the login-policy card describes the local sign-in every install has, so it SHALL render whenever the group does — an install with no reverse proxy is exactly the install whose operator needs to read the emergency URL and account name. A `#organisation` hash SHALL expand the group and scroll to it, through the existing deep-link helper.

The group's trigger SHALL have its own accessible name; the Advanced group's trigger name SHALL be unchanged by the component becoming reusable.

#### Scenario: Nothing configured

- **GIVEN** a signed-in admin holding `security:write` on an install where none of this is configured (no non-password sign-in method is active and there are no rules)
- **WHEN** the Settings page renders
- **THEN** the Organisation group shows one collapsed line reading "Organisation — company login, automatic account management, audit export"
- **AND** that line contains none of the words user, role, SSO, SCIM, IdP or RBAC

#### Scenario: Something configured

- **GIVEN** the same install after one group-to-role rule exists
- **WHEN** the Settings page renders
- **THEN** the collapsed line summarises what is on (company login set up, N sign-in rules) and still contains none of the banned words

#### Scenario: No sign-in method to configure

- **GIVEN** an install whose provider rows contain no reverse-proxy row
- **WHEN** an admin expands the group
- **THEN** the group says there is no company sign-in method to configure yet, and neither the reverse-proxy card nor the rules card is rendered
- **AND** the login-policy card is rendered, because local sign-in exists on every install

#### Scenario: Permission gate

- **GIVEN** a session without `security:write`
- **WHEN** Settings renders on a reverse-proxy install
- **THEN** no Organisation group is rendered and no organisation request is issued

#### Scenario: security:write without users:manage still sees the group

- **GIVEN** a session holding `security:write` whose `accessSummary` is absent
- **WHEN** Settings renders on a reverse-proxy install
- **THEN** the Organisation group renders with the unconfigured one-liner

#### Scenario: Deep link expands the group

- **WHEN** the person opens `/settings#organisation`
- **THEN** the group is expanded and scrolled into view, and the Advanced group stays collapsed
