## ADDED Requirements

### Requirement: Pending identity screen

`/auth/pending` SHALL be a public route rendered by `AuthGate` before the login branch. It SHALL show "Your account is not ready yet", explain that the proxy recognised the person but the dashboard has no account for them, and offer **Try again** (refreshes the session) and **Logout**; when `login.providers` lists `password` and `localPasswordConfigured` is true it SHALL also render the local login form so the break-glass admin can sign in behind the proxy. `AuthGate` SHALL render the same screen — and navigate to `/auth/pending` — whenever the session reports `login.pendingIdentity` for an unauthenticated caller, except on other public routes (`/invite/:token`, `/login…`), which keep their URL. The URL alone renders the pending screen only on a `trusted_header` install. The router SHALL map `/auth/pending` to the dashboard for an authenticated session so a retry after provisioning lands in the app.

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

### Requirement: Invite dialog password-less option

When `login.providers` contains a provider other than `password`, the invite dialog SHALL offer **Add without a password** (naming the provider's label) with an **Identity the proxy sends** field; submitting with it checked SHALL post `ssoOnly: true` and `expectedIdentity: {provider, providerKey, subject}` (subject trimmed, required) and, on `invite: null`, SHALL close with a confirmation toast, refresh the session and show no link dialog. Without a non-password provider the option SHALL NOT render. The pending-invites sheet and the People row menu SHALL mark SSO-only rows ("Awaiting first sign-in through the proxy" instead of an expiry) and SHALL NOT offer **Copy new link** for them. The Password card SHALL derive "a password exists" from `localPasswordConfigured`, not from `passwordRequired`, so a reverse-proxy account without a local password sees **Set password** rather than **Login to manage**.

#### Scenario: Reverse-proxy team adds a colleague

- **GIVEN** the session lists the `trusted_header` provider
- **WHEN** the admin checks Add without a password, enters `sarah@example.com` and submits
- **THEN** the request carries `ssoOnly` and the expected identity and no invite link is shown

## MODIFIED Requirements

### Requirement: Access card composition and tiering

The Settings page SHALL render one Access card (`id="access"`) for everything about who can open the dashboard and how they sign in. The card SHALL render only for sessions holding `write` (the gate the four former cards shared). Its body SHALL follow the store's derived `tier` and `can('users:manage')` and nothing else:

- On the `individual` tier the card SHALL render, above the four controls, one line: for a session with a `user` and `users:manage` the sentence "Only you are using this dashboard — sharing it with others? Each person gets their own sign-in, and you can see who changed what." with an **Invite a teammate** button that opens the invite dialog; for a session with `users:manage` but no `user` (implicit local admin) the sentence "To invite teammates, set a password first." with a **Set password** button that opens the existing password setup dialog. The line SHALL NOT render when the session lacks `users:manage` or when `authMode` is `disabled`. Individual-tier copy SHALL NOT contain the words user, role, SSO, SCIM, IdP or RBAC.
- On the `team` or `enterprise` tier, for a signed-in account (`user` present, whether it signed in with a password or through the reverse proxy) with `users:manage`, the card SHALL render two tabs, **People** and **My sign-in**; People SHALL be selected by default and SHALL contain the People tab, My sign-in SHALL contain the four controls.
- On the `team` or `enterprise` tier otherwise — no `users:manage`, or no account (implicit local admin, disabled-auth principal, or a trusted-header request without an account) — the card SHALL render the four controls only (no tabs, no invite entry), because the backend answers `409 admin_account_required` to account-less principals.

The invite dialog and the one-time invite link dialog SHALL be owned by the card (rendered outside the tier-dependent body) so that the first invite's tier flip cannot unmount them. Creating an account SHALL NOT refresh the session; the session SHALL be refreshed when the link dialog is dismissed, after which the card follows the new tier.

The enterprise tier SHALL add nothing in this release.

#### Scenario: Individual install

- **GIVEN** a signed-in admin whose tier is `individual`
- **WHEN** the Settings page renders
- **THEN** the Access card shows the solo sentence and the Invite a teammate button
- **AND** guest access, password, session and TOTP render inside the card in that order and nowhere else

#### Scenario: Implicit local admin

- **GIVEN** a session with `users:manage` and no `user`
- **WHEN** the Access card renders
- **THEN** the card shows "To invite teammates, set a password first." and a Set password button and no Invite a teammate button

#### Scenario: Team with users:manage

- **GIVEN** the tier is `team` and the session grants `users:manage`, with a password or a reverse-proxy account
- **WHEN** the Access card renders
- **THEN** People and My sign-in tabs are offered with People selected

#### Scenario: Team member without users:manage, or without an account

- **GIVEN** the tier is `team` and the session lacks `users:manage`, or has no `user`
- **WHEN** the Access card renders
- **THEN** only the four sign-in controls render, with no tabs and no invite entry

#### Scenario: First invite survives the tier flip

- **GIVEN** an individual install
- **WHEN** the admin creates the first invite from the solo line
- **THEN** the one-time link stays on screen, the tier is still `individual` until the link dialog is dismissed
- **AND** dismissing it refreshes the session and the card shows the People tab with the new row

### Requirement: Access full page route

`/settings/access` SHALL render the People tab as a page (title, link back to Settings, no card chrome, no "View full page" link) for signed-in accounts holding `users:manage`, on every tier and whatever the sign-in method, and SHALL redirect every other session (no `users:manage` or no `user`) to `/settings`. The route SHALL NOT be a navigation item; the core navigation stays at five items.

#### Scenario: Manager on the individual tier

- **GIVEN** a `users:manage` holder whose tier is `individual`
- **WHEN** `/settings/access` opens
- **THEN** the People page renders

#### Scenario: Non-manager

- **GIVEN** a session without `users:manage`
- **WHEN** `/settings/access` opens
- **THEN** the app redirects to `/settings`
