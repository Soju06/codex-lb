## ADDED Requirements

### Requirement: Access card composition and tiering

The Settings page SHALL render one Access card (`id="access"`) for everything about who can open the dashboard and how they sign in. The card SHALL render only for sessions holding `write` (the gate the four former cards shared). Its body SHALL follow the store's derived `tier` and `can('users:manage')` and nothing else:

- On the `individual` tier the card SHALL render, above the four controls, one line: for a session with a `user` and `users:manage` the sentence "Only you are using this dashboard — sharing it with others? Each person gets their own sign-in, and you can see who changed what." with an **Invite a teammate** button that opens the invite dialog; for a session with `users:manage` but no `user` (implicit local admin) the sentence "To invite teammates, set a password first." with a **Set password** button that opens the existing password setup dialog. The line SHALL NOT render when the session lacks `users:manage` or when `authMode` is not `standard`. Individual-tier copy SHALL NOT contain the words user, role, SSO, SCIM, IdP or RBAC.
- On the `team` or `enterprise` tier, for a signed-in account (`user` present) with `users:manage` on a standard-auth install (`authMode` is `standard`), the card SHALL render two tabs, **People** and **My sign-in**; People SHALL be selected by default and SHALL contain the People tab, My sign-in SHALL contain the four controls.
- On the `team` or `enterprise` tier otherwise — no `users:manage`, no account (implicit local admin, trusted-header or disabled-auth principal), or a non-standard auth mode — the card SHALL render the four controls only (no tabs, no invite entry), because the backend answers `409 admin_account_required` to account-less principals.

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

- **GIVEN** the tier is `team` and the session grants `users:manage`
- **WHEN** the Access card renders
- **THEN** People and My sign-in tabs are offered with People selected

#### Scenario: Team member without users:manage, or without an account

- **GIVEN** the tier is `team` and the session lacks `users:manage`, or has no `user`, or `authMode` is `trusted_header` or `disabled`
- **WHEN** the Access card renders
- **THEN** only the four sign-in controls render, with no tabs and no invite entry

#### Scenario: First invite survives the tier flip

- **GIVEN** an individual install
- **WHEN** the admin creates the first invite from the solo line
- **THEN** the one-time link stays on screen, the tier is still `individual` until the link dialog is dismissed
- **AND** dismissing it refreshes the session and the card shows the People tab with the new row

### Requirement: People tab

The People tab SHALL list every account from `GET /api/dashboard-users` with: display name (username underneath when a display name exists) and a "you" marker on the signed-in account's row; the role as a badge whose hover content lists the role's grants from `GET /api/dashboard-roles` described through `GET /api/dashboard-roles/permissions` (`own` grants suffixed "(own only)"); status (Active, Disabled, or Invited with "Invite expires <relative>" / "Invite expired"); last sign-in (or Never); sign-in method (Password when a password is set, plus a two-factor marker when TOTP is configured). Header actions SHALL be **Invite** (opens the invite dialog), **Pending invites (N)** only when N > 0 (opens the pending-invites sheet), and a quiet **View roles** link (opens the roles sheet). A sign-in requirements line SHALL state whether TOTP is required at sign-in and SHALL point at the existing "Require TOTP on login" control (switching to My sign-in, or linking to `/settings#access` on the full page); no other requirement control SHALL be rendered. When more than eight rows are listed and the tab is inside the card, a **View full page** link to `/settings/access` SHALL render.

Row actions SHALL offer only what the server honours: for active or disabled rows — Change role (dialog whose options are the roles in the session's `assignableRoleIds`, opening on the row's current role), Disable or Enable, Reset two-factor (only when TOTP is configured), Log out everywhere, Delete (confirmation dialog); for invited rows — Copy new link (`POST …/invite`, the new link shown once) and Revoke invite (`DELETE …/invite`). The signed-in account's own row SHALL offer Log out everywhere only, and that action SHALL call the store's `logoutEverywhere()` (a client logout) rather than the admin endpoint. The row of the account named `admin` (the migrated compat account, `409 compat_user_locked`) SHALL offer neither Change role, Disable/Enable nor Delete, and SHALL NOT offer Reset two-factor while the configured "require TOTP on login" policy is on. The TOTP requirement line SHALL read the configured policy from the dashboard settings (`totpRequiredOnLogin` in `GET /api/settings`), not the session's per-login challenge flag, on the card and on `/settings/access`. While that request is pending or has failed the policy is unknown: the line SHALL show a loading state or an error with a retry control instead of a statement, and the `admin` row SHALL NOT offer Reset two-factor (fail closed). Every refusal returned by the server (`last_admin_protected`, `insufficient_delegation`, `compat_user_locked`, `self_modification_forbidden`, `invite_pending`, `invite_not_pending`, `admin_account_required`, `user_not_active`, `user_not_found`, `role_not_assignable`, `username_taken`, `email_taken`, `credential_required`) SHALL be shown inline in the tab's own words (cleared when the next action starts); any other error SHALL show the server message. Errors from the pending-invites sheet SHALL be shown inside the sheet. After every mutation the client SHALL invalidate the users, invites and roles queries (also on `409 invite_not_pending`) and, except for account creation, refresh the session, so that the card falls back to the individual body when the install returns to one account.

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

### Requirement: Invite dialog

The invite dialog SHALL collect a username (required; letters, digits, dots, dashes and underscores, validated before the request), an optional display name and a role. The role options SHALL be exactly the roles whose ids are in the session's `assignableRoleIds`, each with a one-line description; Operator SHALL be preselected when assignable; choosing Admin SHALL show a warning line. When the session's access summary reports a single account and no pending invite, the dialog SHALL show the note "After this invite, the sign-in screen asks for a username. Yours is <username>." Submitting SHALL call `POST /api/dashboard-users`; on success the dialog SHALL replace the form with the invite link (`<origin>/invite/<token>`) shown exactly once, a copy button and the sentence "This link is shown once and expires in 24 hours." `username_taken` SHALL render as an inline field error; other refusals as a banner. No e-mail SHALL be sent or offered.

#### Scenario: First invite

- **GIVEN** an individual install
- **WHEN** the dialog opens and a username is submitted
- **THEN** Operator was preselected, the first-invite note was shown, and the link with the copy button replaces the form

#### Scenario: Admin warning

- **WHEN** Admin is chosen in the role select
- **THEN** the warning line appears

### Requirement: Pending invites and roles sheets

The pending-invites sheet SHALL list `GET /api/dashboard-users/invites` with username, role name and expiry in a scrollable container, and per row **Copy new link** (rotates the token and shows the new link once) and **Revoke invite**; its errors SHALL render inside the sheet. The roles sheet SHALL be read-only: it SHALL show the preset roles in the order Admin, Operator, Member, Viewer, Guest, each with its one-line summary and its grants in plain words; Member SHALL be greyed with a "Coming later" badge; Guest SHALL be labelled "Anonymous, cannot be assigned"; the sheet SHALL state "There are five built-in roles. They cannot be changed." and SHALL offer no clone, edit or create control.

#### Scenario: Roles sheet content

- **WHEN** View roles is activated
- **THEN** the five preset cards render in order with their grants, Member is marked Coming later, Guest is marked as not assignable, and no editing control exists

### Requirement: Access full page route

`/settings/access` SHALL render the People tab as a page (title, link back to Settings, no card chrome, no "View full page" link) for signed-in accounts holding `users:manage` on a standard-auth install, on every tier, and SHALL redirect every other session (no `users:manage`, no `user`, or a non-standard `authMode`) to `/settings`. The route SHALL NOT be a navigation item; the core navigation stays at five items.

#### Scenario: Manager on the individual tier

- **GIVEN** a `users:manage` holder whose tier is `individual`
- **WHEN** `/settings/access` opens
- **THEN** the People page renders

#### Scenario: Non-manager

- **GIVEN** a session without `users:manage`
- **WHEN** `/settings/access` opens
- **THEN** the app redirects to `/settings`

### Requirement: Access card deep links

`/settings#access` SHALL scroll to the Access card and, when tabs are shown, select My sign-in; `/settings#access-people` SHALL scroll to the card and select People. A tab activated by click SHALL stay selected only for the current history entry: any navigation (a new location key, even with an unchanged hash) SHALL re-apply the hash rule. The tabs SHALL be accessible tabs (`tablist`/`tab`/`tabpanel` with keyboard navigation). The TOTP card's own anchor `/settings#totp` SHALL also select My sign-in, so the anchor keeps working once the card sits inside a tab. The helper SHALL live in `features/settings/advanced-settings-deeplink.ts` next to the Advanced group's deep-link rule. The header account menu targets are specified in "Header account menu tiering".

#### Scenario: Hash selects the tab

- **GIVEN** a team-tier manager
- **WHEN** `/settings#access-people` opens
- **THEN** the card is scrolled into view with People selected
- **AND** `/settings#access` opens it with My sign-in selected

## MODIFIED Requirements

### Requirement: Header account menu tiering

On the `individual` tier, or whenever the session has no `user`, the header SHALL render today's Logout button unchanged. On the `team` or `enterprise` tier with a signed-in account the header SHALL instead render an avatar chip showing `username · role name` that opens a menu with: My password (opens the existing change-password dialog), My two-factor (only when the Settings page would render the TOTP card — `write` alias, password management enabled and an active password session; navigates to `/settings#access`, which opens the Access card on the person's own controls where the TOTP card lives; the card also selects that tab for the TOTP card's own `#totp` anchor), Invite teammate (only when the session grants `users:manage`; navigates to `/settings#access-people`), Log out everywhere (`POST /api/dashboard-auth/logout-all`, then least-privilege reset and session refresh), and Log out. The mobile navigation sheet SHALL keep its Logout entry on every tier. The chip's accessible name SHALL be its visible text and the menu SHALL be keyboard operable. Individual-tier copy SHALL NOT contain the words user, role, SSO, SCIM, IdP or RBAC.

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

### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads,
reset priority, prompt-cache affinity TTL, weekly pace controls, limit warm-up
controls, and Fast Mode prohibition), password management
(setup/change/remove), TOTP management (setup/disable), API key auth toggle,
API key management (table, create, edit, delete, regenerate), and
sticky-session administration. API key create/edit controls that expose
reasoning effort choices MUST include upstream-supported extended efforts such
as `max` and `ultra`.

Advanced sections — routing settings, upstream proxy administration, model
sources, firewall, quota phase planner, and sticky-session administration —
SHALL render inside an Advanced settings group that is collapsed by default.
Expanding the group SHALL take exactly one interaction, after which every
previously mandated section SHALL be reachable and fully functional. While the
group is collapsed, its sections SHALL NOT mount, and the sections that
self-fetch on mount — model sources, firewall, quota phase planner, and
sticky-session administration — SHALL NOT issue their data requests; those
requests fire when the group is expanded. The upstream-proxy administration
and accounts queries remain page-level requests issued when the Settings page
loads; their data feeds the advanced Routing and Upstream Proxy sections once
the group is expanded. Core sections (appearance, import, the Access card — which
holds guest access, password management, session and TOTP — and API key
management) SHALL remain visible without expanding the group. Guest access,
password management, session and TOTP SHALL NOT render as separate top-level
cards: they render inside the Access card (see "Access card composition and
tiering") through the same components, in the order guest access, password,
session, TOTP, behind the same gates as before (`write`; session additionally
requires password management to be enabled; TOTP additionally requires a
password-backed session).

#### Scenario: Advanced settings collapsed by default

- **WHEN** a user opens the Settings page
- **THEN** appearance, import, the Access card, and API key management sections are visible
- **AND** the advanced sections (routing, upstream proxy, model sources, firewall, quota planner, sticky sessions) are not mounted
- **AND** the self-fetching sections (model sources, firewall, quota planner, sticky sessions) have not issued their data requests
- **AND** the page-level upstream-proxy admin and accounts queries are still issued on Settings load, feeding the Routing and Upstream Proxy sections once expanded

#### Scenario: One interaction expands every advanced section

- **WHEN** a user activates the Advanced settings group trigger
- **THEN** the routing, upstream proxy, model sources, firewall, quota planner, and sticky-session sections mount and become fully functional

#### Scenario: API key dialog offers extended reasoning efforts

- **WHEN** an operator opens the API key create or edit dialog
- **THEN** the enforced reasoning control offers `Max` and `Ultra` in addition to existing reasoning efforts

#### Scenario: Save weekly pace gap smoothing window

- **GIVEN** the Advanced settings group is expanded
- **WHEN** a user selects a weekly pace gap smoothing window from the routing settings section
- **THEN** the app calls `PUT /api/settings` with `weeklyPaceSmoothingMinutes`
- **AND** the saved settings response reflects the selected value

#### Scenario: Save prompt-cache affinity TTL

- **GIVEN** the Advanced settings group is expanded
- **WHEN** a user updates the prompt-cache affinity TTL from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated TTL and reflects the saved value

#### Scenario: Save staggered idle warm-up setting

- **GIVEN** the Advanced settings group is expanded
- **WHEN** a user toggles staggered idle limit warm-up from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated value and reflects the saved value

#### Scenario: Save Fast Mode prohibition

- **GIVEN** the Advanced settings group is expanded
- **WHEN** a user enables or disables the Fast Mode prohibition control in the routing settings section
- **THEN** the app calls `PUT /api/settings` with `prohibitFastMode`
- **AND** reflects the saved value

#### Scenario: View sticky-session mappings

- **GIVEN** the Advanced settings group is expanded
- **WHEN** a user opens the sticky-session section on the Settings page
- **THEN** the app fetches sticky-session entries and displays each mapping's kind, account, timestamps, and stale/expiry state

#### Scenario: Purge stale prompt-cache mappings

- **GIVEN** the Advanced settings group is expanded
- **WHEN** a user requests a stale purge from the sticky-session section
- **THEN** the app calls the sticky-session purge API and refreshes the list afterward
