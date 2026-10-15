## ADDED Requirements

### Requirement: Organisation settings group

The Settings page SHALL render a collapsed **Organisation** group (`id="organisation"`) after the Advanced settings group, reusing the Advanced group's component so that collapsing genuinely unmounts its children. It SHALL render for every session holding `security:write` — this is where a single-person install first meets the company-login machinery, so the one line is the discovery surface and is drawn before anything is configured. Visibility SHALL be derived from the permission alone, a fact the session already carries: it SHALL NOT depend on `accessSummary`, which is absent for a principal without `users:manage`, and SHALL NOT depend on any request, because none may be issued while the group is collapsed.

While nothing is configured the collapsed line SHALL read as one plain sentence about what the group is for — "Organisation — company login, automatic account management, audit export" — and SHALL NOT contain the words user, role, SSO, SCIM, IdP or RBAC, in any casing. Once `accessSummary` reports something configured (a non-password provider, role mappings, custom roles, SCIM tokens, audit sinks, or a tightened local login policy) the same line SHALL become a status summary of what is on; the summary SHALL name features, not role names, and SHALL obey the same word ban. Expanding SHALL take one interaction and SHALL mount the child cards in order: reverse-proxy card, then group-to-role rules card. A `#organisation` hash SHALL expand the group and scroll to it, through the existing deep-link helper.

The roles both cards offer SHALL be read from `GET /api/role-mappings/assignable-roles` — the read gated by the same `security:write` that gates this group — and NOT from the `users:manage` roles list, so a session holding `security:write` without `users:manage` can still name and choose what its rules hand out. That list already contains exactly the roles the caller may delegate, so the group SHALL NOT filter it again against session state (`assignableRoleIds` is empty for such a session) and SHALL NOT issue the `users:manage` roles request.

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
- **THEN** the group says there is no company sign-in method to configure yet, and neither card is rendered

#### Scenario: Permission gate

- **GIVEN** a session without `security:write`
- **WHEN** Settings renders on a reverse-proxy install
- **THEN** no Organisation group is rendered and no organisation request is issued

#### Scenario: security:write without users:manage still sees the group

- **GIVEN** a session holding `security:write` whose `accessSummary` is absent
- **WHEN** Settings renders on a reverse-proxy install
- **THEN** the Organisation group renders with the unconfigured one-liner

#### Scenario: security:write without users:manage can still choose a role

- **GIVEN** the same session, whose `assignableRoleIds` is empty and which may not read the roles list
- **WHEN** it expands the group
- **THEN** the role pickers are enabled and offer exactly the roles `GET /api/role-mappings/assignable-roles` returned, and the rules and provider controls are editable

#### Scenario: Deep link expands the group

- **WHEN** the person opens `/settings#organisation`
- **THEN** the group is expanded and scrolled into view, and the Advanced group stays collapsed

### Requirement: Reverse-proxy card

Inside the Organisation group, the reverse-proxy card SHALL render whenever a `trusted_header` provider row exists (the built-in row seeded by the provider migration), and SHALL describe the install neutrally — it states how sign-ins arrive today and what each control does, and SHALL NOT imply that a solo or Authelia install is misconfigured. It SHALL show the identity header name and the groups header name as READ-ONLY values with the note that they are set via `CODEX_LB_DASHBOARD_AUTH_PROXY_*`; it SHALL NOT offer an edit control for them. Editable, through `PATCH /api/auth-providers/{id}`: the role an unknown arrival gets (including "refuse"), the role someone matching no rule falls back to (including "disable"), link-by-e-mail, and pausing automatic role updates for this login method. Role choices SHALL use the shared role picker. When the unknown-arrival role is an admin-level preset the card SHALL show a quiet amber note recommending a lower role or refusal, phrased as advice and not as an error. Refusals SHALL be shown inline in the card's own words: `insufficient_delegation`, `role_not_assignable`, `admin_account_required`; step-up is handled by the shared interceptor and needs no control here.

#### Scenario: Header names are shown, not edited

- **WHEN** the card renders
- **THEN** the identity and groups header names are shown as text with the environment-variable note and no input for them

#### Scenario: Editable knobs save

- **WHEN** the admin turns on link-by-e-mail and then pausing automatic role updates
- **THEN** each control sends its own `PATCH /api/auth-providers/{id}` carrying only the field it owns (`linkByEmail`, then `skipRoleSync`) and the card reflects the saved values

#### Scenario: Delegation refusal is explained

- **GIVEN** a `PATCH /api/auth-providers/{id}` that answers `403 insufficient_delegation`
- **WHEN** the card handles it
- **THEN** it shows its own sentence for the refusal, not the raw server message, and the control still shows the saved value

### Requirement: Group-to-role rules card

Inside the Organisation group, the group-to-role rules card SHALL list the rules of the reverse-proxy provider from `GET /api/role-mappings` in priority order, the winner first, each row showing what it matches (a group name, or an e-mail domain) and the role it gives, with edit and delete actions. The order SHALL be changeable with keyboard-reachable Move up / Move down controls (pointer dragging MAY be offered in addition), and a reorder SHALL be written as one `PUT /api/role-mappings/order` carrying the whole order, so a cancelled interaction never leaves a partial order. Adding a rule SHALL use the shared role picker.

With no rules the card SHALL show one honest empty state that says what actually happens today: "Everyone arriving through company login is refused." when the provider refuses unknown arrivals, and "Everyone arriving through company login is admitted as `<role>`." when it admits them (the shipped D10 default). It SHALL NOT promise a refusal the backend would not perform. Alongside it the card SHALL offer a one-click quick-add of "everyone at `<e-mail domain>` as `<role>`", the domain pre-filled from the domain most of the recent refusals came from — the only evidence the dashboard holds about who is knocking — and editable. Before the FIRST rule is saved the card SHALL state that accounts this login method created are re-evaluated the next time each person arrives, and that anyone matching no rule moves to the fall-back role shown on the reverse-proxy card.

The card header SHALL show "N refused sign-ins in the last 7 days" with a **view** action opening a sheet that lists those entries (when, which identity), counted from `GET /api/audit-logs` filtered on `action=login_failed`, `reason=unknown_identity` and the last seven days, whenever N is greater than zero. Because that read is gated on `audit:read`, a session without it SHALL see neither the line nor the action and SHALL NOT issue the request; the rules themselves stay editable. Neither the count nor the sheet SHALL be requested while the group is collapsed. `#organisation-refused` SHALL expand the group with that sheet already open.

#### Scenario: Empty state is honest

- **GIVEN** a reverse-proxy install with no rules whose provider admits unknown arrivals as Admin
- **WHEN** the group is expanded
- **THEN** the card reads "No rules yet." with "Everyone arriving through company login is admitted as Admin." and offers the quick-add pre-filled with the domain most of the recent refusals came from
- **AND** on a provider that refuses unknown arrivals the same card reads "Everyone arriving through company login is refused."

#### Scenario: Quick-add writes one rule

- **WHEN** the admin accepts the quick-add with the member role
- **THEN** one `POST /api/role-mappings` carries `claimName: "email_domain"` and that domain, and the row appears in the list

#### Scenario: First rule warns about re-evaluation

- **WHEN** the admin is about to save the first rule
- **THEN** the card states that existing accounts created by this login method are re-evaluated and that unmatched ones move to the fall-back role

#### Scenario: Reordering is one write

- **GIVEN** three rules
- **WHEN** the admin moves the last one up twice with the keyboard
- **THEN** the list shows the new order and exactly one `PUT /api/role-mappings/order` per interaction carries the full order

#### Scenario: Refused sign-ins with audit access

- **GIVEN** four `login_failed` rows with `reason=unknown_identity` in the last week and a session holding `audit:read`
- **WHEN** the card renders
- **THEN** the header shows "4 refused sign-ins in the last 7 days" with a view action that opens a sheet listing the four identities
- **AND** with no such rows the header shows no refusal line at all

#### Scenario: Refused sign-ins without audit access

- **GIVEN** the same rows and a session without `audit:read`
- **WHEN** the card renders
- **THEN** no refusal line is shown, no audit request is issued, and the rules stay editable

### Requirement: Role picker lists presets first

Every control that hands out a role — the invite dialog, the People tab's role change, the reverse-proxy card and the rules card — SHALL use one shared picker. It SHALL list the presets first, in the fixed preset order, each marked with a lock icon meaning "built in, cannot be edited", and SHALL list custom roles after them under their own heading ONLY when at least one custom role exists; an install with no custom role SHALL see no such heading and no hint of the concept. Options SHALL be limited to the roles the session may delegate (`assignableRoleIds`), so a choice the server would refuse is never offered.

#### Scenario: Presets only

- **GIVEN** an install with no custom roles
- **WHEN** any role picker is opened
- **THEN** the presets are listed in preset order with a lock marker and there is no custom-roles heading

#### Scenario: Custom roles appear once one exists

- **GIVEN** one custom role
- **WHEN** a role picker is opened
- **THEN** the presets are listed first, then the custom role under its own heading

#### Scenario: Only delegable roles are offered

- **GIVEN** a session whose `assignableRoleIds` excludes the admin preset
- **WHEN** a role picker is opened
- **THEN** the admin preset is not offered

### Requirement: Externally managed roles in the People tab

A row whose account has `roleSource` other than `manual` SHALL carry a quiet badge saying its role comes from the company login, and its role change action SHALL open a confirmation explaining that the login method manages this role and that taking over pins it manually; confirming SHALL send `force: true`. A `409 role_managed_externally` returned to a change made without confirmation SHALL be shown in the tab's own words with the take-over offered, not as a raw server message.

#### Scenario: Managed account is marked

- **GIVEN** an account with `roleSource: "mapping"`
- **WHEN** the People tab renders
- **THEN** its row carries the managed-by-company-login badge

#### Scenario: Taking over asks first

- **WHEN** an admin changes that account's role
- **THEN** a confirmation explains the take-over, and confirming sends `force: true` and refreshes the list with the badge gone

#### Scenario: A refusal is explained

- **GIVEN** a change that answers `409 role_managed_externally`
- **WHEN** the tab handles it
- **THEN** it shows its own sentence with the take-over action and no raw server message

## MODIFIED Requirements

### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads,
reset priority, prompt-cache affinity TTL, weekly pace controls, limit warm-up
controls, and Fast Mode prohibition), password management
(setup/change/remove), TOTP management (setup/disable), API key auth toggle,
API key management (table, create, edit, delete, regenerate), and
sticky-session administration. API key create/edit controls that expose
reasoning effort choices MUST include upstream-supported extended efforts such
as `max` and `ultra`. The guest access, password management, session, and
TOTP sections, the API key management section (including the API key auth
toggle), the upstream-proxy administration query and card, and the
sticky-session administration section are write-only surfaces: they SHALL be
mounted, and their data requests issued, only for principals whose session
holds the `write` permission. For read-only principals these surfaces SHALL
NOT mount, SHALL NOT issue their data requests, SHALL NOT render data cached
from an earlier session that held the `write` permission, and SHALL NOT
produce an error banner; the read-only notice remains. When a session loses
the `write` permission, cached responses for these surfaces SHALL be evicted.

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
loads for principals with the `write` permission; their data feeds the
advanced Routing and Upstream Proxy sections once the group is expanded. Core sections (appearance, import, the Access card — which
holds guest access, password management, session and TOTP — and API key
management) SHALL remain visible without expanding the group, subject to the
role qualifiers above. Guest access,
password management, session and TOTP SHALL NOT render as separate top-level
cards: they render inside the Access card (see "Access card composition and
tiering") through the same components, in the order guest access, password,
session, TOTP, behind the same gates as before (`write`; session additionally
requires password management to be enabled; TOTP additionally requires a
password-backed session).

Below the Advanced settings group the page SHALL render at most one further
collapsed group, **Organisation**, for sessions holding `security:write` (see
"Organisation settings group"). It SHALL follow the same contract as the
Advanced group: collapsed by default, one interaction to expand, and while it
is collapsed its cards SHALL NOT mount and SHALL NOT issue their data
requests. These two SHALL remain the only collapsed groups on the page; a
third collapsed layer SHALL NOT be introduced.

#### Scenario: Advanced settings collapsed by default

- **WHEN** a user with the `write` permission opens the Settings page
- **THEN** appearance, import, the Access card, and API key management sections are visible
- **AND** the advanced sections (routing, upstream proxy, model sources, firewall, quota planner, sticky sessions) are not mounted
- **AND** the self-fetching sections (model sources, firewall, quota planner, sticky sessions) have not issued their data requests
- **AND** the page-level upstream-proxy admin and accounts queries are still issued on Settings load, feeding the Routing and Upstream Proxy sections once expanded

#### Scenario: One interaction expands every advanced section

- **WHEN** a user with the `write` permission activates the Advanced settings group trigger
- **THEN** the routing, upstream proxy, model sources, firewall, quota planner, and sticky-session sections mount and become fully functional

#### Scenario: Read-only session does not mount write-only settings surfaces

- **WHEN** a principal whose session lacks the `write` permission opens the Settings page and expands the Advanced settings group
- **THEN** the read-only notice is shown
- **AND** the API key management section, the Upstream Proxy card, and the sticky-session section are not mounted
- **AND** the app does not request `GET /api/api-keys`, `GET /api/settings/upstream-proxy`, or `GET /api/sticky-sessions`
- **AND** no page-level error banner is rendered for those surfaces
- **AND** upstream-proxy data cached from an earlier `write` session, if any, is not rendered
- **AND** the appearance section renders unchanged (its controls are local preferences)
- **AND** the remaining sections (import, reset credits, telemetry, routing, model sources, firewall, quota planner, data retention) render with their controls disabled

#### Scenario: Organisation group is collapsed and silent by default

- **GIVEN** a signed-in admin holding `security:write` on a reverse-proxy install
- **WHEN** the Settings page renders
- **THEN** the Organisation group renders below the Advanced group as a single collapsed line
- **AND** neither the reverse-proxy card nor the group-to-role rules card is mounted
- **AND** the app has not requested `GET /api/auth-providers`, `GET /api/role-mappings`, `GET /api/role-mappings/assignable-roles` or `GET /api/audit-logs`
- **AND** one interaction expands the group, after which both cards mount and issue those requests

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
