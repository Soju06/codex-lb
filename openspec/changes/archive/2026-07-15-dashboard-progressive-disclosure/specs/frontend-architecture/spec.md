## MODIFIED Requirements

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
the group is expanded. Core sections (appearance, import, guest access,
password management, session, TOTP, and API key management) SHALL remain
visible without expanding the group.

#### Scenario: Advanced settings collapsed by default

- **WHEN** a user opens the Settings page
- **THEN** appearance, import, and API key management sections are visible
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

## ADDED Requirements

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
