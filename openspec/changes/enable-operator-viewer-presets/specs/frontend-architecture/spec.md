## ADDED Requirements

### Requirement: Dashboard controls follow the permission their route demands

Every page, section and control whose backend route requires a specific permission SHALL be gated on that permission through the store's `can(permission)` / `usePermission(permission)` selectors, never on the coarse `write` alias or on the wire role `admin` (which every signed-in account carries). In particular: the Conversations view, the request-detail user agent, client IP and conversation fields and the request archive panel follow `conversations:read`; the account credential **Export** action follows `accounts:export`; account actions (pause, resume, probe, warm-up, routing policy, reset) follow `accounts:write`; the API-key page, its queries and the request-log API-key filter follow `api_keys:read` while its create, edit, regenerate, enable/disable and delete controls follow the coarse `write` alias those routes still check (a session with `api_keys:read` alone sees the list without them); guest access, session length, the "Require TOTP on login" toggle, the API-key auth and quota-privacy toggles, firewall entries and proxy endpoint creation follow `security:write`; upstream-proxy administration follows `ops:write`. A session holding `write` without `security:write` (the operator preset) SHALL see the security controls read-only or not at all — never a control that answers `403`: guest access and session length are not rendered, the TOTP card renders the personal enable/disable controls without the requirement toggle, the API-key auth and quota-privacy toggles and the firewall form are disabled, the **Add endpoint** button is disabled. A session with the viewer preset SHALL see the read-only surface a guest sees (Request Logs without Conversations, masked identities, the administrator-only notice on the APIs page, no Access, API-key, upstream-proxy or sticky-session sections) plus its own account menu. The personal password and TOTP controls follow the account, not the alias: they render for any session with password management enabled and an active password session (a Viewer included), the password card additionally for the implicit admin holding `write`; the Access card mounts when either `write` is held or such a personal session exists. Navigation items and their `requires` are unchanged.

#### Scenario: Operator does not see the Conversations view

- **GIVEN** a signed-in account whose permissions are the operator preset's
- **AND** the URL contains `view=conversations`
- **WHEN** the dashboard renders
- **THEN** the effective view is Request Logs, the Conversations option is not offered and no conversation request is enabled
- **AND** the request detail dialog shows neither User Agent, Client IP nor the archive panel

#### Scenario: Operator sees security controls read-only

- **GIVEN** the same session on the Settings page
- **THEN** the API-key auth and quota-privacy toggles are disabled while key management stays enabled
- **AND** after expanding Advanced settings the firewall section is disabled and the upstream-proxy **Add endpoint** button is disabled while pool controls stay enabled
- **AND** the Access card shows the password and TOTP controls only, without guest access or session length, and the TOTP card offers no "Require TOTP on login" toggle

#### Scenario: Export follows accounts:export

- **WHEN** the account actions render for a session without `accounts:export`
- **THEN** no Export button is rendered
- **AND** it is rendered for a session holding `accounts:export`

#### Scenario: Viewer navigation

- **GIVEN** a session whose permissions are the viewer preset's
- **WHEN** the header renders
- **THEN** Dashboard, Reports, Accounts, APIs and Settings are offered, as for a guest

#### Scenario: Viewer keeps its own sign-in controls

- **GIVEN** a fully signed-in Viewer (password management enabled, active password session, no `write`)
- **WHEN** the Settings page renders
- **THEN** the Access card renders with the password and TOTP controls only, and the API-key section does not render
- **AND** the header account menu offers My password and My two-factor but not Invite teammate

#### Scenario: api_keys:read without the write alias

- **GIVEN** a session granting `api_keys:read` but not the `write` alias
- **WHEN** the APIs page renders
- **THEN** the key list and overview render and no create, edit, regenerate, enable/disable or delete control is offered

## MODIFIED Requirements

### Requirement: People tab

The People tab SHALL list every account from `GET /api/dashboard-users` with: display name (username underneath when a display name exists) and a "you" marker on the signed-in account's row; the role as a badge whose hover content lists the role's grants from `GET /api/dashboard-roles` described through `GET /api/dashboard-roles/permissions` (`own` grants suffixed "(own only)"); status (Active, Disabled, or Invited with "Invite expires <relative>" / "Invite expired"); last sign-in (or Never); sign-in method (Password when a password is set, plus a two-factor marker when TOTP is configured). Header actions SHALL be **Invite** (opens the invite dialog), **Pending invites (N)** only when N > 0 (opens the pending-invites sheet), and a quiet **View roles** link (opens the roles sheet). A sign-in requirements line SHALL state whether TOTP is required for everyone at sign-in ("Two-factor is required for everyone at sign-in." / "Two-factor is not required for everyone at sign-in.") and SHALL point at the existing "Require TOTP on login" control (switching to My sign-in, or linking to `/settings#access` on the full page). Above that line, and only for a session holding `security:write`, the tab SHALL render a **Require two-factor for administrators** switch bound to `totpRequiredForAdminRole` from `GET /api/settings`, with the description "Admins and anyone whose role holds a privileged permission must set up two-factor before they can continue." followed, when `adminsWithoutTotpCount` is greater than zero, by "N administrator(s) will have to set up two-factor before they can continue." (already signed-in administrators are held at the enrolment gate on their next request, not at their next sign-in); toggling it SHALL call `PUT /api/settings` with the full form and the new `totpRequiredForAdminRole`, then invalidate the settings query; a `400 invalid_totp_config` answer SHALL be shown inline as "Set up your own two-factor first.", a `409 settings_conflict` answer SHALL additionally invalidate the settings query so the retry carries the fresh version, and any other refusal (including `409 compat_user_locked`) SHALL show the server message. No other requirement control SHALL be rendered. When more than eight rows are listed and the tab is inside the card, a **View full page** link to `/settings/access` SHALL render.

Row actions SHALL offer only what the server honours: for active or disabled rows — Change role (dialog whose options are the roles in the session's `assignableRoleIds`, opening on the row's current role), Disable or Enable, Reset two-factor (only when TOTP is configured), Log out everywhere, Delete (confirmation dialog); for invited rows — Copy new link (`POST …/invite`, the new link shown once) and Revoke invite (`DELETE …/invite`). The signed-in account's own row SHALL offer Log out everywhere only, and that action SHALL call the store's `logoutEverywhere()` (a client logout) rather than the admin endpoint. The row of the account named `admin` (the migrated compat account, `409 compat_user_locked`) SHALL offer neither Change role, Disable/Enable nor Delete, and SHALL NOT offer Reset two-factor while the configured "require TOTP on login" policy is on (the administrator requirement does not lock that row: a reset under it parks the account at the enrolment gate at its next sign-in, which the server allows). The TOTP requirement line SHALL read the configured policy from the dashboard settings (`totpRequiredOnLogin` in `GET /api/settings`), not the session's per-login challenge flag, on the card and on `/settings/access`. While that request is pending or has failed the policy is unknown: the line SHALL show a loading state or an error with a retry control instead of a statement, the administrator switch SHALL NOT render, and the `admin` row SHALL NOT offer Reset two-factor (fail closed). Every refusal returned by the server (`last_admin_protected`, `insufficient_delegation`, `compat_user_locked`, `self_modification_forbidden`, `invite_pending`, `invite_not_pending`, `admin_account_required`, `user_not_active`, `user_not_found`, `role_not_assignable`, `username_taken`, `email_taken`, `credential_required`) SHALL be shown inline in the tab's own words (cleared when the next action starts); any other error SHALL show the server message. Errors from the pending-invites sheet SHALL be shown inside the sheet. After every mutation the client SHALL invalidate the users, invites and roles queries (also on `409 invite_not_pending`) and, except for account creation, refresh the session, so that the card falls back to the individual body when the install returns to one account.

#### Scenario: Rows and markers

- **GIVEN** an admin `admin` (self, TOTP configured), an operator `ops` with display name and an invited viewer `lee`
- **WHEN** the People tab renders
- **THEN** the admin row shows "you", "Active", "Password" and the two-factor marker; the operator row shows the display name with the username underneath; the invited row shows "Invited", the expiry and "Never"
- **AND** Pending invites (1) is offered

#### Scenario: Row-action gating

- **WHEN** the row menus open
- **THEN** the self row offers only Log out everywhere; the operator row offers Change role, Disable, Log out everywhere and Delete; the invited row offers Copy new link and Revoke invite
- **AND** signed in as the operator, the `admin` row offers Reset two-factor and Log out everywhere only — and only Log out everywhere while TOTP is required at sign-in

#### Scenario: Unknown TOTP policy fails closed

- **GIVEN** `GET /api/settings` fails or has not answered
- **WHEN** the People tab renders
- **THEN** no "required" / "not required" statement is shown (an error line with Retry, or a loading state, is)
- **AND** the `admin` row does not offer Reset two-factor
- **AND** Retry re-requests the settings

#### Scenario: Server refusal shown inline

- **WHEN** a disable request is answered `409 last_admin_protected`
- **THEN** the tab shows "At least one active admin must remain." and the row is unchanged

#### Scenario: Revoking the last invite

- **GIVEN** a two-row team whose second row is an invite
- **WHEN** the invite is revoked from the pending-invites sheet
- **THEN** the sheet reads "No pending invites.", the row disappears, the Pending invites button is gone and the session is refreshed

#### Scenario: Administrator two-factor switch

- **GIVEN** a session holding `security:write` and settings reporting `adminsWithoutTotpCount: 2`
- **WHEN** the People tab renders
- **THEN** the switch "Require two-factor for administrators" is off and the hint "2 administrators will have to set up two-factor before they can continue." is shown next to the unchanged global statement
- **WHEN** the switch is toggled
- **THEN** `PUT /api/settings` is called with `totpRequiredForAdminRole: true` and the switch reflects the saved value

#### Scenario: Administrator switch is hidden without security:write

- **GIVEN** a session holding `users:manage` but not `security:write`
- **WHEN** the People tab renders
- **THEN** the global statement is shown and no administrator switch is rendered

#### Scenario: Enable guard worded inline

- **WHEN** toggling the switch is answered `400 invalid_totp_config`
- **THEN** the tab shows "Set up your own two-factor first." and the switch stays off

### Requirement: Header account menu tiering

On the `individual` tier, or whenever the session has no `user`, the header SHALL render today's Logout button unchanged. On the `team` or `enterprise` tier with a signed-in account the header SHALL instead render an avatar chip showing `username · role name` that opens a menu with: My password (opens the existing change-password dialog), My two-factor (only when the Access card would render the TOTP card — password management enabled and an active password session, regardless of the `write` alias, so a Viewer manages its own two-factor; navigates to `/settings#access`, which opens the Access card on the person's own controls where the TOTP card lives; the card also selects that tab for the TOTP card's own `#totp` anchor), Invite teammate (only when the session grants `users:manage`; navigates to `/settings#access-people`), Log out everywhere (`POST /api/dashboard-auth/logout-all`, then least-privilege reset and session refresh), and Log out. The mobile navigation sheet SHALL keep its Logout entry on every tier. The chip's accessible name SHALL be its visible text and the menu SHALL be keyboard operable. Individual-tier copy SHALL NOT contain the words user, role, SSO, SCIM, IdP or RBAC.

#### Scenario: Individual tier

- **GIVEN** the derived tier is `individual`
- **WHEN** the header renders for a signed-in account
- **THEN** the Logout button renders and no account chip is shown

#### Scenario: Team tier

- **GIVEN** the derived tier is `team`, the session has a `user`, and the TOTP card predicate holds
- **WHEN** the chip is opened
- **THEN** My password, My two-factor, Invite teammate (with `users:manage`), Log out everywhere and Log out are offered

#### Scenario: Account without an active password session

- **GIVEN** the derived tier is `team` and the session's `passwordSessionActive` is false
- **WHEN** the chip is opened
- **THEN** My two-factor is not offered

#### Scenario: Invited account without users:manage

- **GIVEN** a signed-in account whose session carries no `accessSummary`
- **WHEN** the header renders
- **THEN** the derived tier is `team` and the chip renders

#### Scenario: Team tier without users:manage

- **GIVEN** the derived tier is `team` and the session's account lacks `users:manage`
- **WHEN** the chip is opened
- **THEN** Invite teammate is not offered

#### Scenario: Implicit admin

- **GIVEN** a session without a `user` (trusted-header, disabled-auth or local bootstrap principal)
- **THEN** the chip is never rendered regardless of tier

#### Scenario: Viewer manages its own two-factor

- **GIVEN** the derived tier is `team`, the session's account holds the viewer preset (no `write`), password management is enabled and the password session is active
- **WHEN** the chip is opened
- **THEN** My password and My two-factor are offered and Invite teammate is not

### Requirement: Guest dashboard hides the Conversations view

The dashboard view selector MUST render the Conversations option only for a
session holding `conversations:read` (the admin preset and custom roles that
grant it). For every other session — guests, viewers, operators — the effective
dashboard view MUST be Request Logs even when the URL contains
`view=conversations`, and the client MUST NOT mount the Conversations view or
issue conversation list/detail API requests. Navigation, filtering, and
conversation detail behavior for sessions holding `conversations:read` MUST
remain unchanged.

#### Scenario: Guest selector hides Conversations

- **GIVEN** the dashboard principal has role `guest`
- **WHEN** the dashboard view selector opens
- **THEN** it exposes Request Logs and does not expose Conversations

#### Scenario: Guest conversation deep link falls back safely

- **GIVEN** the dashboard principal has role `guest`
- **AND** the URL contains `view=conversations`
- **WHEN** the dashboard renders
- **THEN** the effective view is Request Logs
- **AND** the Conversations view is not mounted
- **AND** no `/api/conversations` request is issued

#### Scenario: Conversation access fails closed during auth hydration

- **GIVEN** auth initialization is incomplete and the auth store still has its
  least-privilege default (`guest` role, no permissions)
- **AND** the URL contains `view=conversations`
- **WHEN** the dashboard renders before the session resolves
- **THEN** Request Logs is shown and the Conversations view is not mounted
- **AND** no conversation request is enabled
- **AND** the URL retains `view=conversations`
- **WHEN** the session resolves to a guest principal
- **THEN** the conversation surface remains closed and the URL is normalized to
  Request Logs

#### Scenario: Admin retains Conversations navigation

- **GIVEN** the dashboard session holds `conversations:read`
- **WHEN** the dashboard view selector opens
- **THEN** it exposes both Request Logs and Conversations

#### Scenario: Operator and viewer accounts are treated like guests

- **GIVEN** a signed-in account whose permissions are the operator or viewer preset's
- **AND** the URL contains `view=conversations`
- **WHEN** the dashboard renders
- **THEN** the effective view is Request Logs, the Conversations option is not offered and the URL is normalized
