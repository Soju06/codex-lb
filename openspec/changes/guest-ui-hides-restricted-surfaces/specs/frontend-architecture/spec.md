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
advanced Routing and Upstream Proxy sections once the group is expanded. Core
sections (appearance, import, guest access, password management, session,
TOTP, and API key management) SHALL remain visible without expanding the
group, subject to the role qualifiers above.

#### Scenario: Advanced settings collapsed by default

- **WHEN** a user with the `write` permission opens the Settings page
- **THEN** appearance, import, and API key management sections are visible
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

### Requirement: Guest dashboard does not request restricted surfaces

For a principal whose session lacks the `write` permission, the dashboard
SHALL NOT issue `GET /api/api-keys`, `GET /api/api-keys/{id}/trends`,
`GET /api/api-keys/{id}/usage-7d`, `GET /api/settings/upstream-proxy`,
`GET /api/settings/runtime/connect-address`, or `GET /api/sticky-sessions`
from any page, and SHALL NOT render a control whose action or data depends on
those responses. The APIs page SHALL remain reachable from the core navigation
and SHALL render an administrator-only notice ("API keys are managed by
administrators" with the explanation "Sign in as an administrator to view and
manage API keys.", localized) in place of the API key list, without an error
card or retry control. The Accounts page SHALL hide the Windows OAuth help
toggle for such principals and SHALL render account summaries whose `email` is
masked and whose ChatGPT account id and workspace id are null using the
existing display fallbacks. For such principals the request-log API key filter
control SHALL be hidden, and any `apiKeyId` carried by the URL SHALL be ignored
and removed from the address rather than sent with the request-log or
filter-option requests, so a hidden filter never restricts the results.
Principals whose session holds the `write` permission SHALL observe no change
in the requests issued or controls rendered.

#### Scenario: Guest opens the APIs page

- **WHEN** a principal without the `write` permission navigates to `/apis`
- **THEN** the page heading and subtitle render
- **AND** the administrator-only notice is shown instead of the API key overview, list, and detail
- **AND** the app does not request `GET /api/api-keys`, `GET /api/api-keys/{id}/trends`, or `GET /api/api-keys/{id}/usage-7d`
- **AND** no create, edit, delete, regenerate, or retry control is rendered

#### Scenario: Guest opens the Accounts page

- **WHEN** a principal without the `write` permission navigates to `/accounts`
- **THEN** the app does not request `GET /api/settings/upstream-proxy`
- **AND** the "Need help?" Windows OAuth help toggle is not rendered, so `GET /api/settings/runtime/connect-address` is never requested
- **AND** account summaries with a masked `email` and null ChatGPT account id and workspace id render the masked email and the unknown-workspace fallback

#### Scenario: Guest request-log filters omit API keys

- **WHEN** a principal without the `write` permission views the request-log dashboard
- **THEN** the API key filter control is not rendered
- **AND** the account, model, and status filters remain available

#### Scenario: Guest URL-carried API-key filter is dropped

- **WHEN** a principal without the `write` permission opens `/dashboard?apiKeyId=key_1` (a bookmark, or an administrator's selection retained across logout)
- **THEN** the request-log and filter-option requests carry no `apiKeyId`
- **AND** the `apiKeyId` parameter is removed from the address while other filter parameters are preserved
- **AND** the API key filter control is not rendered

#### Scenario: Writer URL-carried API-key filter is honoured

- **WHEN** a principal with the `write` permission opens `/dashboard?apiKeyId=key_1`
- **THEN** the request-log and filter-option requests carry `apiKeyId=key_1`
- **AND** the API key filter control is rendered

#### Scenario: Writer surfaces are unchanged

- **WHEN** a principal with the `write` permission navigates to `/apis`, `/settings`, or `/accounts`
- **THEN** the app requests `GET /api/api-keys` on `/apis` and `/settings`, `GET /api/settings/upstream-proxy` on `/settings` and `/accounts`, and `GET /api/sticky-sessions` once the Advanced settings group is expanded
- **AND** the API key controls and the Windows OAuth help toggle are rendered
