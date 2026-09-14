## MODIFIED Requirements

### Requirement: People tab

The People tab SHALL list every account from `GET /api/dashboard-users` with: display name (username underneath when a display name exists) and a "you" marker on the signed-in account's row; the role as a badge whose hover content lists the role's grants from `GET /api/dashboard-roles` described through `GET /api/dashboard-roles/permissions` (`own` grants suffixed "(own only)"); status (Active, Disabled, or Invited with "Invite expires <relative>" / "Invite expired"); last sign-in (or Never); sign-in method (Password when a password is set, plus a two-factor marker when TOTP is configured). Header actions SHALL be **Invite** (opens the invite dialog), **Pending invites (N)** only when N > 0 (opens the pending-invites sheet), and a quiet **View roles** link (opens the roles sheet). A sign-in requirements line SHALL state whether TOTP is required for everyone at sign-in ("Two-factor is required for everyone at sign-in." / "Two-factor is not required for everyone at sign-in.") and SHALL point at the existing "Require TOTP on login" control (switching to My sign-in, or linking to `/settings#access` on the full page). Above that line, and only for a session holding `security:write`, the tab SHALL render a **Require two-factor for administrators** switch bound to `totpRequiredForAdminRole` from `GET /api/settings`, with the description "Admins and anyone whose role holds a privileged permission must set up two-factor before they can continue." followed, when `adminsWithoutTotpCount` is greater than zero, by "N administrator(s) will have to set up two-factor before they can continue." (already signed-in administrators are held at the enrolment gate on their next request, not at their next sign-in); toggling it SHALL call `PUT /api/settings` with the full form and the new `totpRequiredForAdminRole`, then invalidate the settings query; a `400 invalid_totp_config` answer SHALL be shown inline as "Set up your own two-factor first.", a `409 settings_conflict` answer SHALL additionally invalidate the settings query so the retry carries the fresh version, and any other refusal SHALL show the server message. No other requirement control SHALL be rendered. When more than eight rows are listed and the tab is inside the card, a **View full page** link to `/settings/access` SHALL render.

Row actions SHALL offer only what the server honours: for active or disabled rows — Change role (dialog whose options are the roles in the session's `assignableRoleIds`, opening on the row's current role), Rename (dialog taking a new username, `PATCH /api/dashboard-users/{id}` with `username`), Disable or Enable, Reset two-factor (only when TOTP is configured), Log out everywhere, Delete (confirmation dialog); for invited rows — Copy new link (`POST …/invite`, the new link shown once) and Revoke invite (`DELETE …/invite`). The signed-in account's own row SHALL offer Log out everywhere only, and that action SHALL call the store's `logoutEverywhere()` (a client logout) rather than the admin endpoint. No row SHALL have actions withheld because of the account it is: the account the install bootstrapped SHALL offer the same menu as any other row, and its refusals SHALL come from the server like everyone else's. The TOTP requirement line SHALL read the configured policy from the dashboard settings (`totpRequiredOnLogin` in `GET /api/settings`), not the session's per-login challenge flag, on the card and on `/settings/access`. While that request is pending or has failed the policy is unknown: the line SHALL show a loading state or an error with a retry control instead of a statement, and the administrator switch SHALL NOT render. Every refusal returned by the server (`last_admin_protected`, `insufficient_delegation`, `self_modification_forbidden`, `invite_pending`, `invite_not_pending`, `admin_account_required`, `user_not_active`, `user_not_found`, `role_not_assignable`, `username_taken`, `email_taken`, `credential_required`) SHALL be shown inline in the tab's own words (cleared when the next action starts); any other error SHALL show the server message. Errors from the pending-invites sheet SHALL be shown inside the sheet. After every mutation the client SHALL invalidate the users, invites and roles queries (also on `409 invite_not_pending`) and, except for account creation, refresh the session, so that the card falls back to the individual body when the install returns to one account.

#### Scenario: Rows and markers

- **GIVEN** an admin `admin` (self, TOTP configured), an operator `ops` with display name and an invited viewer `lee`
- **WHEN** the People tab renders
- **THEN** the admin row shows "you", "Active", "Password" and the two-factor marker; the operator row shows the display name with the username underneath; the invited row shows "Invited", the expiry and "Never"
- **AND** Pending invites (1) is offered

#### Scenario: Row-action gating

- **WHEN** the row menus open
- **THEN** the self row offers only Log out everywhere; the operator row offers Change role, Rename, Disable, Log out everywhere and Delete; the invited row offers Copy new link and Revoke invite
- **AND** signed in as the operator, the migrated `admin` row offers the same menu as the operator row, whatever the sign-in TOTP requirement is

#### Scenario: Renaming from the row menu

- **WHEN** Rename is used on the migrated `admin` row and a free name is submitted
- **THEN** `PATCH /api/dashboard-users/{id}` is called with `username`, the table shows the new name, and a `409 username_taken` answer is shown inline as a taken-name message with the row unchanged

#### Scenario: Unknown TOTP policy fails closed

- **GIVEN** `GET /api/settings` fails or has not answered
- **WHEN** the People tab renders
- **THEN** no "required" / "not required" statement is shown (an error line with Retry, or a loading state, is)
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

### Requirement: Login form username disclosure

The login form SHALL follow the session's `login.usernameField` hint. When it is `hidden` the form SHALL render only the password field plus a link labelled to sign in with a different account that reveals the username field. When it is `shown` the username field SHALL render above the password field, prefilled from `localStorage["codex-lb.last-username"]` and empty when nothing is stored. A remembered username SHALL keep the field visible even when the hint is `hidden`, and the client SHALL forget the remembered username after a successful sign-in whose session reports `login.usernameField` as `hidden`, so an install that returns to a single account shows the field at most once more and is password-only from the next visit; the rule MUST NOT compare the remembered name against any particular username, because the account the install bootstrapped may be renamed. Whenever the username field is visible the form's subtitle SHALL ask for username and password; the password-only form keeps today's sentence. Revealing the field (link or `username_required`) SHALL move focus into it. A `422 username_required` answer SHALL reveal the field with an inline message; `401 invalid_credentials` and `429` SHALL keep the existing generic handling and `401 totp_required` the existing TOTP step. The request SHALL omit `username` when the field is hidden or blank, and after a successful sign-in the client SHALL remember the typed username (and forget the stored one when none was typed). The guest block and the bootstrap screen SHALL remain unchanged, and the bootstrap screen SHALL NOT gain a username field.

#### Scenario: Single-account install

- **GIVEN** `login.usernameField` is `hidden` and nothing is remembered
- **WHEN** the login form renders
- **THEN** only the password field and the different-account link are shown
- **AND** activating the link reveals the username field

#### Scenario: Multi-account install prefills the last username

- **GIVEN** `login.usernameField` is `shown` and `alice` is remembered
- **WHEN** the login form renders
- **THEN** the username field is shown with `alice`

#### Scenario: Remembered username prevents flapping, then converges

- **GIVEN** `login.usernameField` is `hidden` and `alice` is remembered
- **WHEN** the login form renders
- **THEN** the username field is shown with `alice`
- **WHEN** that sign-in succeeds and the session still reports `hidden`
- **THEN** the remembered username is forgotten and the next render shows the password field alone

#### Scenario: Server demands a username

- **WHEN** a password-only submit is answered with `422 username_required`
- **THEN** the username field appears with an inline message and the password error banner is not shown
