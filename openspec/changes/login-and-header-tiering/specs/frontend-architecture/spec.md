## ADDED Requirements

### Requirement: Public routes render before the login branch

The auth gate SHALL read the current location and SHALL render public routes before its login branch, so that a public route renders for a visitor without a session and for a signed-in principal alike and is never replaced by the login form, the TOTP dialog or the bootstrap screen. In this release the only public route is `/invite/:token`, which SHALL render the invite acceptance screen keyed by the token. All other paths SHALL keep the existing gate behaviour.

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

### Requirement: Session request failure offers a retry

When the session request fails (network error, proxy 5xx) and the store therefore holds no authenticated session, the auth gate SHALL render the route-recovery error surface whose retry re-requests the session, instead of falling through to the router and its not-found page.

#### Scenario: Session endpoint answers 502

- **WHEN** `GET /api/dashboard-auth/session` fails on load
- **THEN** the error surface renders with a retry control
- **AND** activating it requests the session again

### Requirement: Invite acceptance screen

The invite acceptance screen SHALL first show a checking state while it fetches `GET /api/dashboard-auth/invite/{token}`, then the role name and inviter, a username field prefilled with the suggested username (read-only when the invite locks it), an optional display name, and a password with confirmation validated with the same client-side rules as password setup. Submitting SHALL call `POST /api/dashboard-auth/invite/accept`; on success the client SHALL refresh the session and navigate to `/`, so that a new session reporting `totpEnrollmentRequired` lands on the authenticator enrollment gate before any app page renders. Submitting SHALL go through the store so the returned session is applied without an extra request. The client-side username rule SHALL be `^[a-z0-9._-]{1,64}$` (case-insensitive). For a `404` on lookup or accept the screen SHALL show exactly one message ("This invite has expired. Ask the person who invited you for a new link.") and no other detail about the token; any other lookup failure (`429`, network, 5xx) SHALL show the server message with a "Try again" control that re-runs the lookup. The signed-in message with a Logout button SHALL be shown whenever the session has a `user` — including a password session still pending TOTP — and whenever accept answers `409 already_signed_in` (refreshing the session first when the client did not know the account); `username_taken` and `username_locked` SHALL render as inline field errors.

#### Scenario: Valid link to sign-in

- **WHEN** a visitor opens a valid invite link, sees the checking state, then enters matching passwords and submits
- **THEN** the accept request carries the token, the username and the password
- **AND** the session is refreshed and the app navigates to `/`

#### Scenario: Dead link

- **WHEN** a visitor opens an expired, consumed, revoked or unknown invite link
- **THEN** the screen shows only the single expiry message
- **AND** no role, inviter, form or retry control is rendered

#### Scenario: Rate-limited lookup

- **WHEN** the lookup answers `429`
- **THEN** the rate-limit message and a "Try again" control are shown instead of the expiry message
- **AND** activating the control re-requests the lookup

#### Scenario: Locked username

- **WHEN** the invite locks the username
- **THEN** the username field is read-only and the suggested username is sent unchanged

### Requirement: Login form username disclosure

The login form SHALL follow the session's `login.usernameField` hint. When it is `hidden` the form SHALL render only the password field plus a link labelled to sign in with a different account that reveals the username field. When it is `shown` the username field SHALL render above the password field, prefilled from `localStorage["codex-lb.last-username"]` and empty when nothing is stored. A remembered username other than the default `admin` SHALL keep the field visible even when the hint is `hidden`. Whenever the username field is visible the form's subtitle SHALL ask for username and password; the password-only form keeps today's sentence. Revealing the field (link or `username_required`) SHALL move focus into it. A `422 username_required` answer SHALL reveal the field with an inline message; `401 invalid_credentials` and `429` SHALL keep the existing generic handling and `401 totp_required` the existing TOTP step. The request SHALL omit `username` when the field is hidden or blank, and after a successful sign-in the client SHALL remember the typed username (and forget the stored one when none was typed). The guest block and the bootstrap screen SHALL remain unchanged.

#### Scenario: Single-account install

- **GIVEN** `login.usernameField` is `hidden` and nothing is remembered
- **WHEN** the login form renders
- **THEN** only the password field and the different-account link are shown
- **AND** activating the link reveals the username field

#### Scenario: Multi-account install prefills the last username

- **GIVEN** `login.usernameField` is `shown` and `alice` is remembered
- **WHEN** the login form renders
- **THEN** the username field is shown with `alice`

#### Scenario: Remembered username prevents flapping

- **GIVEN** `login.usernameField` is `hidden` and `alice` is remembered
- **WHEN** the login form renders
- **THEN** the username field is shown with `alice`

#### Scenario: Server demands a username

- **WHEN** a password-only submit is answered with `422 username_required`
- **THEN** the username field appears with an inline message and the password error banner is not shown

### Requirement: Authenticator enrollment gate

When the session is authenticated and reports `totpEnrollmentRequired`, the auth gate SHALL render an enrollment screen (QR code, secret and code confirmation through `/api/dashboard-auth/totp/setup/start` and `/confirm`, plus a Logout button) instead of the app. After a successful confirmation the gate SHALL verify the confirmed code with `/api/dashboard-auth/totp/verify` and apply the returned session; if that verification fails it SHALL refresh the session so the regular TOTP dialog takes over. It SHALL render the app once the session no longer reports enrollment. The Settings TOTP setup dialog and the enrollment gate SHALL share one enrollment form component.

#### Scenario: Enrollment required after sign-in

- **GIVEN** an authenticated session with `totpEnrollmentRequired: true`
- **WHEN** the gate renders
- **THEN** the enrollment screen is shown and the app is not
- **AND** confirming a code verifies it and applies the verified session without a second TOTP prompt

### Requirement: Disclosure tier is a pure function

The client SHALL derive how much account machinery to show from `resolveDisclosureTier(accessSummary, user)` and from nothing else. When `accessSummary` is `null` (the server sends no summary to principals without `users:manage`) the result SHALL be `team` if the session has a signed-in account (`user`) — such an account can only exist because someone was invited — and `individual` otherwise (guest, visitor, implicit admin). With a summary, `enterprise` SHALL be returned when `providersEnabled` contains a kind other than `password`, or `customRoles`, `scimTokens`, `auditSinks` or `roleMappings` is at least 1, or `localLoginPolicy` is not `enabled`. Otherwise `team` SHALL be returned when `usersTotal` is at least 2, `pendingInvites` is at least 1, or `nonAdminUsers` is at least 1; otherwise `individual`. Components SHALL read the store's derived `tier` and SHALL NOT combine summary facts themselves.

#### Scenario: Matrix

- **WHEN** each rule input is toggled individually on a single-account summary
- **THEN** the team inputs each yield `team`, the enterprise inputs each yield `enterprise`, a `null` summary yields `individual` without an account and `team` with one, and enterprise wins over team

### Requirement: Header account menu tiering

On the `individual` tier, or whenever the session has no `user`, the header SHALL render today's Logout button unchanged. On the `team` or `enterprise` tier with a signed-in account the header SHALL instead render an avatar chip showing `username · role name` that opens a menu with: My password (opens the existing change-password dialog), My two-factor (only when the Settings page would render the TOTP card — `write` alias, password management enabled and an active password session; navigates to `/settings#totp`, the TOTP card's anchor), Log out everywhere (`POST /api/dashboard-auth/logout-all`, then least-privilege reset and session refresh), and Log out. The mobile navigation sheet SHALL keep its Logout entry on every tier. The chip's accessible name SHALL be its visible text and the menu SHALL be keyboard operable. Individual-tier copy SHALL NOT contain the words user, role, SSO, SCIM, IdP or RBAC.

#### Scenario: Individual tier

- **GIVEN** the derived tier is `individual`
- **WHEN** the header renders for a signed-in account
- **THEN** the Logout button renders and no account chip is shown

#### Scenario: Team tier

- **GIVEN** the derived tier is `team`, the session has a `user`, and the TOTP card predicate holds
- **WHEN** the chip is opened
- **THEN** My password, My two-factor, Log out everywhere and Log out are offered

#### Scenario: Account without an active password session

- **GIVEN** the derived tier is `team` and the session's `passwordSessionActive` is false
- **WHEN** the chip is opened
- **THEN** My two-factor is not offered

#### Scenario: Invited account without users:manage

- **GIVEN** a signed-in account whose session carries no `accessSummary`
- **WHEN** the header renders
- **THEN** the derived tier is `team` and the chip renders

#### Scenario: Implicit admin

- **GIVEN** a session without a `user` (trusted-header, disabled-auth or local bootstrap principal)
- **THEN** the chip is never rendered regardless of tier

## MODIFIED Requirements

### Requirement: Header navigation progressive disclosure

The application header SHALL render core destinations — Dashboard, Reports,
Accounts, APIs, and Settings — as top-level navigation items. Non-core
destinations (currently Automations) SHALL NOT render as top-level items: on
desktop they SHALL be reachable through an Advanced menu that opens in one
interaction, and in the mobile navigation menu they SHALL be grouped under an
Advanced label. Direct routes to non-core destinations (e.g. `/automations`)
SHALL continue to resolve, and the legacy `/firewall` route SHALL continue to
redirect to `/settings`. A new page-level navigation destination SHALL default
to the Advanced menu unless a spec explicitly designates it as core.

Every navigation item SHALL carry `requires: Permission` metadata mirroring the
permission the backend requires for the page's reads (`accounts:read` for
Accounts, `dashboard:read` for Dashboard, Reports, APIs, Settings and
Automations). The header SHALL render only the items whose permission the
session grants at any scope (`<permission>:all` or `<permission>:own` in
`permissions`), and a guarded route the session cannot use SHALL redirect to
`/dashboard` (rendering the not-found surface only when `dashboard:read` itself
is missing). The metadata SHALL NOT add items: the core set stays the five
destinations above and guests, who hold `dashboard:read` and `accounts:read`,
SHALL see exactly those five.

#### Scenario: Advanced menu reveals Automations

- **WHEN** a user opens the Advanced menu in the header
- **THEN** an Automations item is revealed
- **AND** activating it navigates to `/automations`

#### Scenario: Automations is not a top-level item

- **WHEN** a user views the header navigation
- **THEN** Dashboard, Reports, Accounts, APIs, and Settings render as top-level links
- **AND** Automations does not render as a top-level link

#### Scenario: Advanced trigger reflects the active route

- **WHEN** the current route is an advanced destination such as `/automations`
- **THEN** the Advanced menu trigger renders in the active state
- **AND** on core routes it renders in the inactive state

#### Scenario: Deep links to advanced destinations keep working

- **WHEN** a user opens `/automations` directly
- **THEN** the Automations page renders

#### Scenario: Legacy firewall route redirects

- **WHEN** a user opens `/firewall`
- **THEN** the app redirects to `/settings`

#### Scenario: Items follow the session's grants

- **GIVEN** a session whose grants include `dashboard:read` but not `accounts:read`
- **WHEN** the header renders
- **THEN** Dashboard, Reports, APIs and Settings render and Accounts does not
- **AND** opening `/accounts` directly redirects to `/dashboard`

#### Scenario: Guests keep today's navigation

- **GIVEN** a guest session (`dashboard:read` and `accounts:read`)
- **WHEN** the header renders
- **THEN** all five core destinations render
