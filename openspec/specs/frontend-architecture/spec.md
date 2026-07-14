# frontend-architecture Specification

## Purpose

Define dashboard surface contracts so settings, account management, and operational views stay coherent across the SPA.
## Requirements
### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads,
reset priority, prompt-cache affinity TTL, weekly pace controls, limit warm-up
controls, and Fast Mode prohibition), password management
(setup/change/remove), TOTP management (setup/disable), API key auth toggle,
API key management (table, create, edit, delete, regenerate), and
sticky-session administration. API key create/edit controls that expose
reasoning effort choices MUST include upstream-supported extended efforts such
as `max` and `ultra`.

#### Scenario: API key dialog offers extended reasoning efforts

- **WHEN** an operator opens the API key create or edit dialog
- **THEN** the enforced reasoning control offers `Max` and `Ultra` in addition to existing reasoning efforts

#### Scenario: Save weekly pace gap smoothing window

- **WHEN** a user selects a weekly pace gap smoothing window from the routing settings section
- **THEN** the app calls `PUT /api/settings` with `weeklyPaceSmoothingMinutes`
- **AND** the saved settings response reflects the selected value

#### Scenario: Save prompt-cache affinity TTL

- **WHEN** a user updates the prompt-cache affinity TTL from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated TTL and reflects the saved value

#### Scenario: Save staggered idle warm-up setting

- **WHEN** a user toggles staggered idle limit warm-up from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated value and reflects the saved value

#### Scenario: Save Fast Mode prohibition

- **WHEN** a user enables or disables the Fast Mode prohibition control in the routing settings section
- **THEN** the app calls `PUT /api/settings` with `prohibitFastMode`
- **AND** reflects the saved value

#### Scenario: View sticky-session mappings

- **WHEN** a user opens the sticky-session section on the Settings page
- **THEN** the app fetches sticky-session entries and displays each mapping's kind, account, timestamps, and stale/expiry state

#### Scenario: Purge stale prompt-cache mappings

- **WHEN** a user requests a stale purge from the sticky-session section
- **THEN** the app calls the sticky-session purge API and refreshes the list afterward

### Requirement: Accounts page

The Accounts page SHALL display a two-column layout: left panel with searchable account list, import button, and add account button; right panel with selected account details including usage, token info, and actions (pause/resume/delete/re-authenticate). The Accounts page SHALL also let operators view and update whether an account is authorized for upstream cybersecurity work without losing existing account actions such as pause, resume, re-authenticate, export, and delete.

The layout SHALL fit mobile, tablet, and desktop dashboard widths without horizontal page overflow caused by fixed-width account controls.

The Accounts page SHALL keep the add account button outside the scrollable account list so it remains reachable without scrolling through existing accounts, and SHALL keep long account lists in a bounded internal scroll region on desktop so account rows do not push the page layout past the selected-account detail panel.

Account status displays and filters SHALL distinguish `reauth_required` accounts from `deactivated` accounts: `reauth_required` means the local credential/session must be refreshed by operator re-authentication, while `deactivated` means the upstream account is disabled, suspended, deleted, or explicitly deactivated.

#### Scenario: Account security-work authorization is toggled

- **WHEN** an operator toggles Trusted Access for Cyber for an account
- **THEN** the app sends the account update request with the requested `securityWorkAuthorized` value
- **AND** the account list and dashboard overview data are invalidated after the update succeeds

#### Scenario: Security-work authorization appears in account summaries

- **WHEN** an account summary has `securityWorkAuthorized=true`
- **THEN** the Accounts page shows that account as eligible for Trusted Access for Cyber routing

#### Scenario: Same-email workspace slots are distinguishable

- **WHEN** the account list contains multiple accounts with the same email
- **AND** at least one account has workspace metadata
- **THEN** the list and detail views show workspace identity or compact account id context sufficient to distinguish the credential slots

#### Scenario: Same-login workspace slots are preserved

- **WHEN** multiple imported or OAuth-completed credentials share the same ChatGPT account identity
- **AND** they carry distinct workspace ids or workspace labels
- **THEN** each workspace credential is preserved as a separate local account slot

#### Scenario: Import copy reflects credential slots

- **WHEN** a user views import settings
- **THEN** the copy describes preserving separate workspace or unknown credential slots instead of email-level duplicates

#### Scenario: Responsive account management layout

- **WHEN** the Accounts page is rendered at a mobile-width viewport
- **THEN** the account list and selected account detail stack vertically
- **AND** account list filters, quota rows, proxy controls, routing policy controls, token status, and action buttons fit within the viewport without horizontal document overflow

#### Scenario: Add account remains outside account list scrolling

- **WHEN** the Accounts page renders the account list controls
- **THEN** the add account button is not a child of the scrollable account list
- **AND** the button remains available without scrolling through existing accounts

#### Scenario: Long account list scrolls inside the left panel

- **WHEN** the Accounts page renders more account rows than fit in the visible left panel
- **THEN** the account rows scroll inside the account list region
- **AND** the add account action remains visible outside that scroll region

#### Scenario: Re-authentication-required account is labeled separately

- **WHEN** an account summary has `status = "reauth_required"`
- **THEN** the account list and account detail status badge show `Re-auth required`
- **AND** the account can be found with the status filter for `reauth_required`
- **AND** the account detail exposes the re-authenticate action
- **AND** the account detail does not expose pause or resume actions that could bypass re-authentication
- **AND** the account list and account detail do not expose routing-policy controls that imply the account is selectable while operator recovery is required

### Requirement: Request logs display account plan tier
When a request log entry is associated with an account, the dashboard request-log API response MUST expose the persisted request-log `planType` snapshot, and the recent-requests table MUST render the plan tier in a visible request-log column or badge.

#### Scenario: Request log entry keeps its original plan type snapshot
- **WHEN** a request log entry is written while the associated account's `plan_type` is `free`
- **AND** the account later changes to `team`
- **THEN** the `GET /api/request-logs` response still includes `planType: "free"` for that row
- **AND** the dashboard recent-requests table renders the original `free` plan tier visibly for that row

#### Scenario: Legacy request log entry without account still renders
- **WHEN** a request log entry has no related account
- **THEN** the `GET /api/request-logs` response includes `planType: null` or omits it
- **AND** the dashboard recent-requests table still renders the row without failing

### Requirement: Request logs distinguish actual and requested service tiers
When a request log entry includes service-tier data, the dashboard request-log API response MUST expose the billable tier, requested tier, and actual tier separately. The recent-requests UI MUST display the actual tier when available and MUST show the requested tier when it differs from the visible actual tier.

#### Scenario: Dashboard shows upstream-selected tier and requested tier
- **WHEN** a request log entry is recorded with `requested_service_tier: "priority"`, `actual_service_tier: "default"`, and billable `service_tier: "default"`
- **THEN** the `GET /api/request-logs` response includes `requestedServiceTier: "priority"`, `actualServiceTier: "default"`, and `serviceTier: "default"`
- **AND** the dashboard renders the model label with `default`
- **AND** the dashboard also shows that the request asked for `priority`

### Requirement: Accounts list surfaces quota reset timing

The Accounts page account list SHALL render a compact 5h quota row and a weekly quota row for accounts that have both quota windows, and SHALL include the time remaining until reset for each rendered row when a reset timestamp is available. Weekly-only accounts SHALL omit the 5h row. Account cards SHALL also surface whether the account is opted in to limit warm-up.

#### Scenario: Regular account shows both quota rows
- **WHEN** the account list renders an account with both primary and weekly quota windows
- **THEN** the list item shows both 5h and weekly quota rows
- **AND** each rendered row shows its reset countdown

#### Scenario: Weekly-only account omits the 5h row
- **WHEN** the account list renders an account whose primary window is absent
- **THEN** the list item does not render a 5h quota row
- **AND** the weekly quota row still renders

#### Scenario: Limit warm-up opt-in is visible
- **WHEN** the account list renders an account with limit warm-up opt-in enabled
- **THEN** the account card shows the opt-in state

### Requirement: Accounts list respects compact row appearance preference
The Accounts page account list SHALL honor a locally stored appearance preference that selects which compact quota rows are shown: 5h, weekly, or both. The default preference SHALL be Both. When the selected row is unavailable for a given account, the list MAY fall back to the available row so the account still shows quota information.

#### Scenario: Default preference shows both rows
- **WHEN** the appearance preference is unset
- **THEN** the account list shows both 5h and weekly rows for accounts that have both quota windows

#### Scenario: 5h preference shows only the 5h row
- **WHEN** the appearance preference is set to 5H
- **THEN** the account list shows the 5h row and hides the weekly row for accounts that have both quota windows

#### Scenario: Weekly preference shows only the weekly row
- **WHEN** the appearance preference is set to W
- **THEN** the account list shows the weekly row and hides the 5h row for accounts that have both quota windows

### Requirement: Accounts list orders by next reset
The Accounts page account list SHALL order accounts by the earliest upcoming quota reset timestamp among the rendered quota windows. Accounts without any reset timestamp SHALL sort after accounts with a reset timestamp. When reset timestamps are equal or unavailable, the list MAY fall back to a stable text-based order.

#### Scenario: Earlier reset sorts first
- **WHEN** two accounts are shown in the account list and one account has an earlier quota reset time than the other
- **THEN** the earlier-reset account appears before the later-reset account

### Requirement: Dashboard request-log filtering supports API keys

The dashboard request logs view SHALL allow operators to filter rows by one or more API keys using stable API key identifiers while presenting human-readable API key labels in the UI.

#### Scenario: Apply API key request-log filter

- **WHEN** a user selects one or more API keys in the request logs filters
- **THEN** the request logs query refetches from `GET /api/request-logs` with repeated `apiKeyId` parameters
- **AND** the dashboard overview is NOT refetched

#### Scenario: Request-log API key options remain expandable

- **WHEN** a user has already selected one API key in the request logs filters
- **THEN** the API key filter options continue to show other matching API keys instead of collapsing to only the selected key
- **AND** the user can add another API key without clearing the existing selection first

### Requirement: Dashboard weekly credits pace

The dashboard SHALL show weekly quota pace when account weekly capacity credits, remaining credits, reset time, and window length are available. The pace calculation MUST use credit totals rather than averaging per-account percentages, because weekly ChatGPT quota credits are not the same unit as raw request tokens. The dashboard MUST prefer the backend-provided `weeklyCreditPace` object from `GET /api/dashboard/overview` when present, and MAY fall back to a local calculation only for older responses that do not include that field. The dashboard projections payload SHALL expose smoothed weekly pace gap fields for display while preserving instantaneous live usage fields.

#### Scenario: Weekly credits pace uses account reset deadlines

- **WHEN** multiple accounts have weekly quota data with different `resetAtSecondary` values
- **THEN** the system computes each account's expected remaining weekly credits from that account's own reset time and window length before summing totals

#### Scenario: Weekly credits pace excludes hard-blocked or stale usage rows

- **WHEN** an account is `reauth_required`, paused, deactivated, missing from the account table, or its latest weekly usage sample is older than the freshness window derived from the usage refresh interval
- **THEN** the account is not included in weekly pace totals or forecasts
- **AND** the response reports the excluded stale account count separately from the included account count

#### Scenario: Exhausted accounts still count in weekly credits pace

- **WHEN** an account is `rate_limited` or `quota_exceeded`
- **AND** it has complete, fresh weekly capacity, remaining credits, reset time, and window length
- **THEN** the account is included in weekly pace totals and forecasts

#### Scenario: Current schedule gap is separate from forecast shortfall

- **WHEN** actual remaining weekly credits are lower than scheduled remaining weekly credits
- **THEN** the response reports `scheduleGapCredits` for the current deficit against the linear schedule
- **AND** the response reports `projectedShortfallCredits` only for a future shortfall forecast based on recent burn
- **AND** the dashboard labels the two concepts separately
- **AND** the dashboard describes the current deficit as over planned usage, fewer credits remaining than scheduled, or equivalent over-consumption wording rather than "behind schedule"

#### Scenario: Displayed pace gap uses configured smoothing

- **GIVEN** the weekly pace gap smoothing window is configured
- **WHEN** recent weekly usage samples are available for the current weekly reset/window segment
- **THEN** the response includes `smoothedDeltaPercent`, `smoothedScheduleGapCredits`, and `paceGapSmoothingMinutes`
- **AND** the Weekly credits pace card displays the smoothed gap while keeping `actualUsedPercent` as the live current value

#### Scenario: Weekly pace smoothing resets with quota window

- **GIVEN** a smoothing time window contains samples from before and after a weekly quota reset
- **WHEN** the latest sample belongs to the new reset/window segment
- **THEN** the smoothed pace gap excludes the samples from the previous reset/window segment

#### Scenario: Forecast burn uses recent weekly usage slope

- **WHEN** an account has high cumulative weekly usage from earlier in the window but no recent increase in weekly used percent
- **THEN** the projected shortfall forecast is based on the recent slope and does not assume the earlier full-window average continues

#### Scenario: Near-reset depletion is not a false alarm

- **WHEN** an account has consumed 99% of its weekly quota and 99% of its weekly window has elapsed
- **THEN** the weekly pace treats that account as on pace rather than over plan

#### Scenario: Missing weekly credit data is omitted

- **WHEN** an account is missing weekly capacity credits, remaining credits, reset time, or window length
- **THEN** that account is omitted from weekly pace calculation

#### Scenario: No valid weekly credit data hides pace

- **WHEN** no account has complete, fresh weekly credits pace data for an `active`, `rate_limited`, or `quota_exceeded` account
- **THEN** the dashboard does not render a fake weekly pace value

### Requirement: Account weekly trend planned line

The account detail usage trend SHALL include an ideal weekly remaining line when weekly reset timing is available, so operators can compare actual weekly remaining credits against the linear schedule between weekly resets.

#### Scenario: Weekly trend shows planned depletion between resets

- **WHEN** account trend buckets include weekly reset time and window length
- **THEN** the account 7-day trend includes a dashed weekly plan line computed from each bucket's reset deadline and window length

#### Scenario: Weekly trend plan restarts after reset

- **WHEN** weekly trend buckets cross into a new reset window with a new reset deadline
- **THEN** the planned line jumps back toward full remaining capacity for the new weekly window instead of continuing one global diagonal

### Requirement: Dashboard request-log list excludes deleted-account rows

When an account is deleted, request-log rows that were soft-deleted as part of that account removal MUST NOT appear in the dashboard request-log list or request-log filter-option facets.

#### Scenario: Deleted account log hidden from recent request rows

- **GIVEN** a request log row was previously associated with an account
- **AND** deleting that account soft-deleted the row
- **WHEN** a user loads `GET /api/request-logs`
- **THEN** the soft-deleted row is not included in the `requests` payload

#### Scenario: Deleted account log hidden from request-log facets

- **GIVEN** a request log row was previously associated with an account
- **AND** deleting that account soft-deleted the row
- **WHEN** a user loads `GET /api/request-logs/options`
- **THEN** the soft-deleted row does not contribute account, model, API-key, or status facet options

### Requirement: Dashboard overview metrics keep soft-deleted request logs

Dashboard overview request metrics and trends MUST continue to aggregate soft-deleted request-log rows so account deletion does not rewrite historical request activity.

#### Scenario: Deleted account log still counted in overview metrics

- **GIVEN** an account has request-log activity within the active overview timeframe
- **AND** the account is deleted afterward
- **WHEN** a user loads `GET /api/dashboard/overview`
- **THEN** request-derived metrics and trends still include that historical request-log activity

### Requirement: Dashboard settings page exposes password session lifetime

The SPA settings page SHALL expose a dashboard password session lifetime control for operators when password management is enabled. The control SHALL display the current configured lifetime, validate an operator-supplied value against the backend minimum, and save the new lifetime through the existing settings API. When the configured lifetime exceeds 30 days, the SPA SHALL show a warning that the longer lifetime increases the impact of a leaked browser profile or stolen cookie.

#### Scenario: Admin updates dashboard password session lifetime

- **WHEN** an admin opens the Settings page and changes the dashboard session lifetime value
- **THEN** the SPA submits the updated lifetime through `/api/settings`
- **AND** the saved settings response reflects the new lifetime value

#### Scenario: Admin chooses a long dashboard session lifetime

- **WHEN** an admin enters a dashboard session lifetime greater than 30 days
- **THEN** the Settings page shows a warning explaining that the longer lifetime increases the impact of a leaked browser profile or stolen cookie
- **AND** the admin can still save the configured lifetime

### Requirement: Account summary duplicate email indicator

The dashboard accounts API SHALL expose an `isEmailDuplicate` boolean on each
`AccountSummary` returned by `GET /api/accounts`. The field MUST be `true` when
another account row in the same response has the same real email address and
the same ChatGPT account identity, and MUST be `false` for unique real
email/identity pairs. Missing, blank, and legacy placeholder emails equal to
`DEFAULT_EMAIL` (`unknown@example.com`) MUST be excluded from duplicate
detection and MUST NOT be flagged as duplicates. Rows that share an email but
belong to different ChatGPT account identities MUST NOT be flagged as
duplicates.

#### Scenario: Duplicate real email and identity pairs are flagged

- **WHEN** `GET /api/accounts` returns two or more account rows with the same real non-placeholder email and the same ChatGPT account identity
- **THEN** every row in that email and identity group includes `isEmailDuplicate: true`

#### Scenario: Same email across identities is not flagged

- **WHEN** `GET /api/accounts` returns account rows with the same real non-placeholder email but different ChatGPT account identities
- **THEN** those rows include `isEmailDuplicate: false`

#### Scenario: Placeholder emails are ignored

- **WHEN** `GET /api/accounts` returns two or more account rows whose email is `unknown@example.com`
- **THEN** those rows include `isEmailDuplicate: false`

#### Scenario: Unique emails are not flagged

- **WHEN** `GET /api/accounts` returns an account row with an email that appears only once in the response
- **THEN** that row includes `isEmailDuplicate: false`

### Requirement: Dashboard projections load after the primary dashboard data

The dashboard SPA SHALL render primary dashboard content from `GET /api/dashboard/overview`
and recent request-log data without waiting for depletion or weekly-credit projection
calculations. Projection-only data, including safe-line depletion markers and weekly-credit
pace, SHALL be available from `GET /api/dashboard/projections` and fetched after overview
data is available.

#### Scenario: Main dashboard renders before projections finish

- **GIVEN** an authenticated operator opens the dashboard
- **WHEN** `GET /api/dashboard/overview` and request-log calls complete before `GET /api/dashboard/projections`
- **THEN** the dashboard renders the primary cards, usage donuts, account list, and request-log surface
- **AND** projection-only safe-line and weekly-credit fields may populate later when the projections response arrives

#### Scenario: Projection endpoint exposes heavy dashboard calculations

- **WHEN** the dashboard client requests `GET /api/dashboard/projections`
- **THEN** the response includes depletion safe-line data and weekly-credit pace data when those calculations are available
- **AND** the overview endpoint does not need to compute those fields for initial page render

### Requirement: Dashboard usage donuts present credits as stacked remaining and capacity

The dashboard's primary and secondary usage donuts MUST present remaining credits and capacity as two stacked values separated by a horizontal divider: the remaining count above (bold, `data-testid="donut-center-remaining"`) and the capacity count below (muted, `data-testid="donut-center-capacity"`). Both values MUST use locale-aware thousands separators (e.g. `7,331` and `7,560`). Compact-format abbreviation (e.g. `7.33k`) MUST NOT be used in the donut center for these panels.

The primary donut title MUST read `5-Hour Credits`. The secondary donut title MUST read `Weekly Credits`.

#### Scenario: Dashboard donut shows stacked remaining and capacity

- **WHEN** the dashboard renders a usage donut with `remaining=7331` and `total=7560`
- **THEN** the donut title reads `5-Hour Credits` or `Weekly Credits`
- **AND** the center renders `7,331` in the remaining element and `7,560` in the capacity element
- **AND** a divider separates the two values

### Requirement: API sidebar shows pooled credit bars

The APIs page left sidebar SHALL render pooled credit bars on each API key list item. Each bar SHALL display a label, percentage, and colored progress bar using the same `MiniQuotaBar` component as the Accounts sidebar.

Labels SHALL be "Pooled 5h" for the primary window and "Pooled Weekly" for the secondary window. No reset countdown text SHALL be shown.

When `pooledCapacityCreditsPrimary > 0` and `pooledRemainingPercentPrimary` is not null, the "Pooled 5h" bar SHALL be visible. Otherwise it SHALL be hidden. The "Pooled Weekly" bar SHALL be visible when `pooledRemainingPercentSecondary` is not null.

When both bars are visible, they SHALL be laid out in a 2-column grid. When only one bar is visible, it SHALL use a 1-column layout.

When API key limit rules exist, the sidebar SHALL also render the legacy limit progress bar below the pooled bars with an "API Limit" label and percentage value so it remains clearly distinct from the pooled-account bars.

#### Scenario: Both pooled bars visible

- **WHEN** an API key has both primary and secondary pooled credit data
- **THEN** the sidebar item shows "Pooled 5h" and "Pooled Weekly" bars in a 2-column grid

#### Scenario: Primary bar hidden for free-tier accounts

- **WHEN** an API key's pooled primary capacity is 0
- **THEN** only the "Pooled Weekly" bar is shown in a 1-column layout

#### Scenario: No credit data hides bars

- **WHEN** an API key has no pooled credit data
- **THEN** no credit bars are rendered on that list item

#### Scenario: API limit bar is labeled distinctly

- **WHEN** an API key has configured limit rules
- **THEN** the sidebar renders the legacy limit bar with an "API Limit" label below the pooled bars

### Requirement: Footer version update indicator

The dashboard footer SHALL show the running application version and SHALL display a compact update-available icon next to that version only when the runtime version API confirms a newer stable GitHub release exists.

#### Scenario: Newer release is available

- **WHEN** `GET /api/runtime/version` returns `updateAvailable: true` with a `latestVersion`
- **THEN** the footer renders an accessible update icon beside the current version
- **AND** the icon links to `https://github.com/Soju06/codex-lb/releases/latest`
- **AND** the icon title or accessible label includes the latest version

#### Scenario: Version lookup is unavailable

- **WHEN** `GET /api/runtime/version` fails or returns no newer version
- **THEN** the footer continues showing the current version without an update indicator

### Requirement: Delete account with history purge

The account delete confirmation dialog SHALL include a checkbox labeled "Delete all history for this account". When checked and the delete action is confirmed, all associated data (request_logs, usage_history, sticky_sessions) SHALL be hard-deleted from the database instead of soft-deleted. When unchecked, the existing soft-delete behavior SHALL apply.

#### Scenario: Delete with history checkbox checked

- **WHEN** an operator opens the delete confirmation dialog for an account and checks "Delete all history for this account"
- **AND** clicks the confirm/Delete button
- **THEN** the `DELETE /api/accounts/{account_id}` request includes `?delete_history=true`
- **AND** all `request_logs` rows for the account are hard-deleted from the database
- **AND** `usage_history` rows for the account are hard-deleted (existing behavior)
- **AND** the account itself is deleted
- **AND** the UI shows a success toast and refreshes the account list

#### Scenario: Delete with history checkbox unchecked

- **WHEN** an operator opens the delete confirmation dialog and does NOT check "Delete all history for this account"
- **AND** clicks the confirm/Delete button
- **THEN** the `DELETE /api/accounts/{account_id}` request omits the `delete_history` parameter
- **AND** `request_logs` rows are soft-deleted (account_id=NULL, deleted_at set)
- **AND** all other behavior is identical to current account deletion

#### Scenario: Cancel the delete dialog

- **WHEN** an operator opens the delete confirmation dialog
- **AND** clicks the Cancel button
- **THEN** the dialog closes and no API request is made
- **AND** the account remains in the list unchanged

### Requirement: Dashboard limit warm-up controls

The dashboard SHALL expose global limit warm-up controls in Settings and per-account opt-in/status in account views. The global default SHALL be disabled. Settings SHALL include an exhausted-threshold percent control that determines which pre-reset usage samples count as exhausted for reset-confirmed warm-up.

#### Scenario: Configure warm-up behavior
- **WHEN** an operator opens Settings
- **THEN** the dashboard shows controls for enabling limit warm-up, selecting primary/secondary/both windows, setting the warm-up model, setting the prompt, setting the exhausted threshold, and setting the cooldown

#### Scenario: Validate warm-up settings before save
- **WHEN** an operator edits warm-up model, prompt, exhausted threshold, or cooldown fields
- **THEN** the dashboard enforces the same non-empty, max-length, percent, and integer cooldown bounds as the backend API before enabling save

#### Scenario: Show per-account opt-in and last attempt
- **WHEN** account summaries include limit warm-up status
- **THEN** the dashboard shows whether warm-up is enabled for that account
- **AND** it shows the latest attempt window, status, model, and completion/attempt time when available

#### Scenario: Warm-up controls are accessible by name
- **WHEN** an operator navigates the dashboard with assistive technology
- **THEN** global and per-account warm-up toggles expose descriptive accessible names that identify the setting and account context

### Requirement: Account alias contract

The dashboard accounts API SHALL expose an operator-controlled, human-readable `alias` on every account summary, and SHALL provide an endpoint that lets an authenticated dashboard session set or clear that alias. The alias MUST be persisted on the `Account` record and MUST be reflected in `AccountSummary.alias`. When a non-empty alias is set, the same `AccountSummary.display_name` field MUST resolve to the alias so consumers that already render `display_name` see the operator's chosen label without further changes. When the alias is null or cleared, `display_name` MUST fall back to the account's email so existing UI continues to identify the account.

#### Scenario: Listing surfaces the alias when set

- **WHEN** the dashboard requests `GET /api/accounts` and at least one account has a stored alias
- **THEN** that account's summary includes `alias` with the stored value
- **AND** its `display_name` equals the alias

#### Scenario: Listing falls back to email when alias is null

- **WHEN** the dashboard requests `GET /api/accounts` and an account has no stored alias
- **THEN** that account's summary includes `alias: null`
- **AND** its `display_name` equals the account's email

#### Scenario: Setting an alias persists and trims whitespace

- **WHEN** an authenticated dashboard session calls `PUT /api/accounts/{account_id}/alias` with `{"alias": "  Personal Plus  "}`
- **THEN** the response is 200 with `{"account_id": "...", "alias": "Personal Plus"}`
- **AND** subsequent `GET /api/accounts` reflects the trimmed value on both `alias` and `display_name`

#### Scenario: Empty or whitespace-only alias clears the value

- **WHEN** an authenticated dashboard session calls `PUT /api/accounts/{account_id}/alias` with `{"alias": ""}` or `{"alias": "   "}`
- **THEN** the response is 200 with `{"alias": null}`
- **AND** subsequent `GET /api/accounts` shows `alias: null` and `display_name` reverting to the account's email

#### Scenario: Setting alias on an unknown account returns 404

- **WHEN** `PUT /api/accounts/{account_id}/alias` is called with an `account_id` that does not exist
- **THEN** the response is 404 with error code `account_not_found`

#### Scenario: Dashboard UI edits and searches aliases

- **WHEN** an operator opens the dashboard accounts page and selects an account
- **THEN** the account detail panel provides an `Account alias` control that can save a non-empty alias through `PUT /api/accounts/{account_id}/alias`
- **AND** clearing the control stores `alias: null` and restores the email fallback
- **AND** account search matches the stored alias or alias-backed display name so operators can filter duplicate-email accounts by their chosen label

### Requirement: APIs tab shows a 7-day account-cost donut for selected API keys

When the selected API key's 7-day usage payload contains one or more `accountCosts[]` items, the APIs tab detail panel SHALL render the account-cost donut section and usage-trend section inside a single shared card. On large screens, the split layout SHALL use a 25:75 width ratio with the donut on the left, the trend on the right, and a vertical separator between them.

The donut section SHALL include a title and subtitle, SHALL show the 7-day total cost in the donut center, SHALL not render a separate `Total $...` summary in the section header, and SHALL render the legend below the donut.

#### Scenario: Donut renders inside the shared usage card
- **WHEN** a selected API key has 7-day account-cost data and trend data
- **THEN** the detail panel renders the account-cost donut section to the left of the trend section inside one shared card
- **AND** the large-screen layout uses a 25:75 split with a vertical separator between the sections

#### Scenario: Donut is omitted when no account-cost buckets exist
- **WHEN** the selected API key's `usage-7d.accountCosts[]` array is empty
- **THEN** the APIs tab does not render the account-cost donut card

### Requirement: APIs tab account-cost donut uses existing account labels and privacy rules

The donut legend SHALL use the account label derived from the existing payload fields: `Deleted Account` for `isDeleted: true`, otherwise the account `email` when present, otherwise `Unknown Account`. Non-deleted account labels MUST respect the hide-account-info privacy setting used elsewhere in the dashboard.

The legend SHALL show each visible bucket's 7-day cost, SHALL coordinate hover highlighting with the matching pie slice, and SHALL use the same vertically scrollable five-row viewport pattern as the dashboard donuts when more rows exist than fit without scrolling.

#### Scenario: Deleted account label is explicit
- **WHEN** an `accountCosts[]` item has `isDeleted: true`
- **THEN** the legend label is `Deleted Account`

#### Scenario: Privacy hiding applies to non-deleted account labels
- **WHEN** the hide-account-info setting is enabled
- **AND** a visible donut legend row represents a non-deleted account label
- **THEN** the label text is privacy-blurred

#### Scenario: Legend scroll viewport matches dashboard donuts
- **WHEN** more than five account-cost buckets are present
- **THEN** the donut legend keeps all rows available
- **AND** the visible legend viewport shows five rows before scrolling

### Requirement: APIs tab account-cost donut follows the dashboard donut visual system

The account-cost donut SHALL use the same sizing, palette generation, reduced-motion behavior, hover-linked legend highlighting, and gray consumed/deleted color treatment as the dashboard donut visual system.

#### Scenario: Deleted-account slice uses the consumed gray color
- **WHEN** the donut renders a deleted-account bucket
- **THEN** that bucket uses the same gray color family used by the dashboard donut's consumed or used segment

### Requirement: APIs tab usage trend control layout is compact in the split view

The APIs tab usage trend card SHALL keep its heading and subtitle, SHALL align the accumulated toggle and Tokens/Cost legend to the right side of the heading block on larger screens, and SHALL reduce the chart right margin to fit the split layout.

#### Scenario: Usage trend controls align with the heading row
- **WHEN** the usage trend card renders
- **THEN** the Tokens/Cost legend appears to the right of the heading block on larger screens
- **AND** the accumulated toggle remains in the same right-side controls group

#### Scenario: Usage trend uses compact right margin
- **WHEN** the usage trend chart renders in the split APIs-tab layout
- **THEN** the chart right margin is reduced from the previous wider layout to a compact right margin

### Requirement: Dashboard account summaries sorted by primary capacity

The dashboard overview API MUST return account summaries sorted by `capacity_credits_primary` in descending order so the highest-capacity accounts appear first. Accounts with no primary capacity MUST sort after accounts that have one.

#### Scenario: Accounts ordered by primary capacity

- **WHEN** the dashboard overview response includes multiple accounts with different `capacity_credits_primary` values
- **THEN** accounts are ordered from highest to lowest primary capacity

#### Scenario: Accounts without primary capacity sort last

- **WHEN** an account has `capacity_credits_primary` of `null` or `0`
- **THEN** that account appears after accounts with a positive primary capacity

### Requirement: Account card row height is 11.5rem

The dashboard account card viewport MUST use 11.5rem per visible row.

#### Scenario: Account card max height

- **WHEN** the account cards container renders with `ACCOUNT_CARD_VISIBLE_ROWS=2`
- **THEN** the container `maxHeight` is `calc(2 * 11.5rem + 1rem)`

### Requirement: Weekly credits pace header uses flex-start alignment

The weekly credits pace card header MUST align the title and gauge icon to the flex start, not vertically centered.

#### Scenario: Header alignment

- **WHEN** the weekly credits pace card renders
- **THEN** the header row uses `justify-between` without `items-center`

### Requirement: Request logs expose cost breakdown details
When a request log has sufficient usage data, the dashboard request-log API MUST expose raw input/output token counts and a cost breakdown that separates non-cached input, cached input, and output cost.

#### Scenario: Successful request log exposes token and cost segments
- **WHEN** a successful request log row has persisted input, cached-input, and output usage
- **THEN** `GET /api/request-logs` includes `inputTokens`, `outputTokens`, and `costBreakdown`
- **AND** `costBreakdown` includes `inputUsd`, `cachedInputUsd`, `outputUsd`, and `totalUsd`

#### Scenario: Request log output falls back to reasoning tokens
- **WHEN** a successful request log row has no persisted `output_tokens` and does have `reasoning_tokens`
- **THEN** `GET /api/request-logs` uses the reasoning-token value for `outputTokens`

#### Scenario: Request log response preserves shape for legacy partial data
- **WHEN** a successful request log row is missing one or more persisted token or cost segments
- **THEN** `GET /api/request-logs` still includes `inputTokens`, `outputTokens`, and `costBreakdown`
- **AND** any unavailable top-level token field is returned as `null`
- **AND** `costBreakdown` includes `inputUsd`, `cachedInputUsd`, `outputUsd`, and `totalUsd`
- **AND** any unavailable `costBreakdown` field is returned as `null`
- **AND** clients can render only the available token and cost segments without treating the row as invalid

### Requirement: Request detail dialog renders successful cost breakdowns
The dashboard request-log `View Details` dialog MUST render a `Cost` section under `Archive` for successful request rows and MUST hide the section for non-success rows.

#### Scenario: Successful request displays ordered cost details
- **WHEN** a request log detail dialog opens for an `ok` row with available breakdown data
- **THEN** the dialog displays the total cost first
- **AND** the dialog lists available cost segments in this order: input, cached, output
- **AND** each displayed segment includes its token count and matching currency value
- **AND** token counts use the same compact formatting as the request-log tokens column
- **AND** currency values are rounded to two decimals

#### Scenario: Missing cost segments are omitted without breaking the dialog
- **WHEN** a successful request log row is missing one or more token or cost segments
- **THEN** the dialog renders only the available segments
- **AND** if no segments are available the `Cost` section is hidden

### Requirement: Reports page renders English user-facing labels

The dashboard SHALL render `/reports` with the following exact page-owned user-facing labels for the current reports surface:

- `Cost Report`
- `Usage history by date range`
- `Loading...`
- `Total Cost`
- `Requests`
- `Cost by Day`
- `Tokens by Day`
- `Distribution by Model`
- `Distribution by UserAgent`
- `Daily Breakdown`
- `Day`
- `Input Tokens`
- `Output Tokens`
- `Cost`
- `Accounts`
- `Total`
- `Failed to load report data:`
- `Failed to load model and user-agent options:`
- `Failed to load account options:`
- `Some report data could not be loaded. Try reloading.`
- `Retry`

Backend-provided strings, account values, model values, and raw server error payload text SHALL remain out of scope for this wording change unless `/reports` renders page-owned labels around them.

#### Scenario: Reports page shows English labels

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page title is `Cost Report`
- **AND** the subtitle is `Usage history by date range`
- **AND** the summary cards include `Total Cost` and `Requests`
- **AND** the chart and table section titles include `Cost by Day`, `Tokens by Day`, `Distribution by Model`, `Distribution by UserAgent`, and `Daily Breakdown`
- **AND** the daily table headings include `Day`, `Input Tokens`, `Output Tokens`, `Cost`, and `Accounts`

#### Scenario: Reports page state labels are English

- **WHEN** `/reports` renders a loading, empty, or error state
- **THEN** the loading label is `Loading...`
- **AND** page-owned error wrappers use `Failed to load report data:`, `Failed to load model and user-agent options:`, and `Failed to load account options:` when those failures render
- **AND** the retry warning is `Some report data could not be loaded. Try reloading.`
- **AND** the retry button label is `Retry`

### Requirement: Reports distribution donuts show compact active-metric totals

The `/reports` page SHALL render both `Distribution by Model` and `Distribution by UserAgent` cards with a donut-center total that uses the page-owned label `Total` above the current metric value.
When a donut card is in `cost` mode, its center total and legend values SHALL display compact USD values with up to two fractional digits and `K`, `M`, or `B` suffixes when applicable.
When a donut card is in `req` mode, its center total and legend values SHALL display compact request values with up to two fractional digits and `K`, `M`, or `B` suffixes when applicable.

#### Scenario: Reports distribution donut totals switch with the selected metric

- **WHEN** `/reports` renders model or user-agent distribution data
- **THEN** each distribution donut shows `Total` in the center above the total value
- **AND** `cost` mode uses compact USD totals such as `$1.43K`
- **AND** `req` mode uses compact request totals such as `1.5B`

### Requirement: Reports page loads report data from the reports endpoint

The `/reports` page SHALL load and refetch report data from `GET /api/reports`.

#### Scenario: Reports page loads from reports endpoint

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page loads report data from `GET /api/reports`

#### Scenario: Reports page refetches from reports endpoint

- **WHEN** an authenticated operator changes a report filter on `/reports`
- **THEN** the page refetches report data from `GET /api/reports`

### Requirement: Reports page exposes visible filter controls

The `/reports` page SHALL expose visible filter controls for `7d`, `30d`, and `90d` quick presets, start date, end date, account, and model. When an authenticated operator clicks one of the quick presets, the page SHALL visibly highlight that preset. When the operator manually edits the start date or end date afterward, the page SHALL clear the quick-preset highlight until another quick preset is clicked. The start and end date inputs SHALL disallow selecting dates later than the browser's current local calendar date.

#### Scenario: Reports page shows report filter controls

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page exposes visible filter controls for `7d`, `30d`, and `90d` quick presets, start date, end date, account, and model

#### Scenario: Quick preset highlight follows the selected preset

- **WHEN** an authenticated operator clicks the `30d` quick preset on `/reports`
- **THEN** the page visibly highlights the `30d` preset
- **AND** the page updates the start and end dates to the `30d` preset range

#### Scenario: Quick preset highlight clears after manual date edits

- **WHEN** an authenticated operator clicks a quick preset on `/reports`
- **AND** then manually edits the start date or end date
- **THEN** the page clears the quick-preset highlight
- **AND** the page keeps the edited date range values

#### Scenario: Report date inputs disallow future dates

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the start date and end date inputs prevent selecting a date later than the browser's current local calendar date

### Requirement: Reports page preserves reports query parameter names

Requests from `/reports` to `GET /api/reports` SHALL use the query parameter names `startDate`, `endDate`, `accountId`, and `model`.

#### Scenario: Reports page uses preserved reports query parameter names

- **WHEN** an authenticated operator opens `/reports` or changes a report filter
- **THEN** the request uses `startDate`, `endDate`, `accountId`, and `model` as the query parameter names

### Requirement: Reports chart tooltip uses recharts TooltipContentProps

The reports `ChartTooltip` component SHALL type its props as `Partial<TooltipContentProps>` from recharts so that context-injected properties (`payload`, `active`, `label`, `coordinate`) are optional at the JSX call site while remaining correctly typed inside the component body.

#### Scenario: ChartTooltip renders without context props at the JSX call site

- **WHEN** a reports chart passes `<ChartTooltip names={...} formatValue={...} />` via the recharts `<Tooltip content={...}>` prop
- **THEN** TypeScript compilation succeeds without errors about missing `payload`, `active`, `label`, or `coordinate`
- **AND** recharts injects those properties at runtime before calling the component

### Requirement: Accounts page unified export action

The Accounts page SHALL render a single "Export" button in the account actions area. Clicking the export button SHALL open a modal dialog titled "Auth Export" with a format mode selector ("codex" / "opencode"). The page SHALL use a single API call to `POST /api/accounts/{id}/export/auth` before opening the modal, and SHALL pass the full response to the modal for display. No auto-download SHALL occur without user interaction in the modal.

#### Scenario: Single export button replaces dual buttons

- **WHEN** a user views the account actions for a selected account
- **THEN** exactly one "Export" button is visible
- **AND** no separate "Export OpenCode auth" button is present

#### Scenario: Export opens modal after API success

- **WHEN** a user clicks the "Export" button
- **THEN** the frontend calls `POST /api/accounts/{id}/export/auth`
- **AND** on success the "Auth Export" modal opens with the response data
- **AND** no file is downloaded until the user clicks Download in the modal

#### Scenario: Export error shows toast

- **WHEN** the `POST /api/accounts/{id}/export/auth` call fails
- **THEN** a toast notification shows the error message
- **AND** no modal opens

### Requirement: Reset-window routing setting UI
The dashboard routing settings UI SHALL expose a control for the earlier-reset
preference window whenever earlier-reset routing preference is configurable. The
control SHALL allow only `primary` and `secondary` values and SHALL submit the
selected value using the settings API field `preferEarlierResetWindow`.

#### Scenario: Operator selects primary reset window
- **GIVEN** the routing settings UI is open
- **WHEN** the operator selects `primary` as the earlier-reset window
- **THEN** the settings update payload includes `preferEarlierResetWindow: "primary"`

#### Scenario: Imported settings preserve reset-window preference
- **GIVEN** an imported settings payload includes `preferEarlierResetWindow`
- **WHEN** the settings import is applied
- **THEN** the imported value is sent to the backend instead of being dropped

### Requirement: Dashboard account cards show live credit state

Account summary responses SHALL expose the latest upstream credit metadata for
each account as nullable `creditsHas`, `creditsUnlimited`, and `creditsBalance`
fields. The dashboard account schema SHALL accept those fields.

The dashboard account card SHALL render a compact Credits row. If
`creditsUnlimited` is true, the value SHALL be `Unlimited`. Otherwise, when a
numeric credit balance is available it SHALL render that balance. If no credit
balance is available, the card MAY fall back to the account's remaining weekly
or primary credit value, and SHALL render `-` when no credit value is known.

#### Scenario: Unlimited credits render explicitly

- **WHEN** an account summary has `creditsUnlimited = true`
- **THEN** the dashboard account card shows `Credits: Unlimited`

#### Scenario: Positive credit balance renders on the card

- **WHEN** an account summary includes `creditsBalance = 1.5`
- **THEN** the dashboard account card shows that numeric credit balance

#### Scenario: Missing credit data renders a placeholder

- **WHEN** an account summary has no credit balance and no remaining credit fallback
- **THEN** the dashboard account card shows `Credits: -`

### Requirement: Dashboard settings must expose upstream proxy routing controls
The settings dashboard MUST allow operators to inspect upstream proxy routing state, enable or disable routing, choose the default proxy pool, create proxy endpoints, create proxy pools, and add endpoints to pools.

#### Scenario: Operator creates a pool from existing endpoints
- **GIVEN** the upstream proxy admin API returns at least one endpoint
- **WHEN** an operator creates a pool and selects endpoint members
- **THEN** the dashboard MUST call the pool creation API with the selected endpoint ids
- **AND** refresh the displayed upstream proxy admin state.

### Requirement: Dashboard accounts must expose account proxy bindings
The accounts dashboard MUST allow operators to bind an account to a proxy pool and disable an existing account binding.

#### Scenario: Operator binds an account to a pool
- **GIVEN** upstream proxy routing has at least one proxy pool
- **WHEN** an operator selects a pool for an account and saves the binding
- **THEN** the dashboard MUST call the account binding API for that account
- **AND** display the selected pool as the account binding.

### Requirement: Accounts list supports explicit sort modes

The Accounts page account list SHALL expose sort modes for reset time
soonest-first, reset time latest-first, account name ascending, and account name
descending. The default sort mode SHALL remain reset time soonest-first. The
same selected sort mode SHALL apply to both the rendered account list and the
page-level selected-account fallback.

#### Scenario: Reset soonest remains the default

- **WHEN** the account list renders without an explicit sort mode
- **THEN** accounts with the earliest upcoming visible quota reset sort first

#### Scenario: Reset latest sorts finite resets descending

- **WHEN** a user selects reset time latest-first
- **THEN** accounts with later upcoming visible quota resets sort before
  accounts with earlier upcoming visible quota resets
- **AND** accounts without an upcoming visible reset timestamp sort after
  accounts with finite upcoming reset timestamps

#### Scenario: Name sort modes order by account label

- **WHEN** a user selects account name ascending or descending
- **THEN** the account list orders accounts by display name, email, or account
  identifier in the selected direction

### Requirement: API key overview SHALL show lifetime usage aggregates

The dashboard API key overview SHALL present usage totals using the API key list
`usageSummary` values as lifetime aggregates (all non-warmup request-log history),
unless the backend contract is changed to provide a bounded window explicitly.

#### Scenario: Overview usage labels reflect lifetime scope

- **WHEN** the API key overview renders `usageSummary` values for request count,
  token count, and cost
- **THEN** the section labels SHALL read as lifetime usage (for example:
  "Lifetime Requests", "Lifetime Cost", "Lifetime Cost by API Key", "Lifetime Tokens
  by API Key"), and SHALL NOT be labeled as 7-day totals.

### Requirement: Dashboard request-log details expose user-agent metadata
The dashboard request-log API response MUST expose the persisted request-log `useragent` and `useragentGroup` values when present. The Request Details dialog MUST render the full `useragent` value in a `User Agent` field below the `Transport`, `Time`, and `Error Code` row, and MUST render `—` when no full user-agent value is stored.

#### Scenario: Request details show the full stored user-agent
- **WHEN** a request log entry is stored with `useragent: "opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"` and `useragentGroup: "opencode"`
- **THEN** the `GET /api/request-logs` response includes both values for that row
- **AND** the Request Details dialog shows `User Agent` with the full stored string

#### Scenario: Request details show a placeholder for legacy rows
- **WHEN** a request log entry has `useragent: null`
- **THEN** the `GET /api/request-logs` response includes `useragent: null` and `useragentGroup: null` or omits them as nullable fields
- **AND** the Request Details dialog renders `User Agent` as `—`

### Requirement: `/api/reports` returns nullable account buckets safely

`GET /api/reports` SHALL return an `accountId` field for each `byAccount` item that is either a string account identifier or `null`.
The system MUST preserve rows with `account_id IS NULL` and return them as a separate account bucket with `accountId: null` so historical usage is still represented.

#### Scenario: Null accountId is serialized for historical rows
- **WHEN** request logs in the selected period include rows with `account_id = NULL`
- **AND** those rows have non-null `cost_usd`
- **THEN** the `byAccount` response includes an item with `accountId: null`
- **AND** response serialization succeeds without schema validation failure

### Requirement: Reports data path uses backend-side date grouping

`GET /api/reports` SHALL use backend-side date grouping logic for both PostgreSQL and SQLite, producing `YYYY-MM-DD` daily buckets for stable trend display and CSV export.

#### Scenario: SQLite report request returns date buckets
- **WHEN** the repository is SQLite
- **AND** `/api/reports` is called with a valid date range
- **THEN** the response contains `daily` entries with `date` values in `YYYY-MM-DD` format
- **AND** the endpoint responds with HTTP 200

### Requirement: Reports API is accessible through the dashboard route map

The dashboard surface SHALL expose a reports page at route `/reports` and route to data loaded from `GET /api/reports` with `startDate`, `endDate`, `accountId`, and `model` filters.

#### Scenario: Dashboard reports page uses `/api/reports`
- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page loads the aggregated reports payload from `GET /api/reports`
- **AND** allows filtering by date range, model, and account
- **AND** uses the returned payload to render summary cards, daily charts, and model and user-agent distribution donuts

### Requirement: Reports distribution donuts show active-metric totals

The `/reports` page SHALL render both `Distribution by Model` and `Distribution by UserAgent` cards.
Each card SHALL show `Total` above the donut center value.
When the distribution metric toggle is `cost`, the center value and legend values SHALL show compact USD formatting with up to two decimal places and `K`, `M`, or `B` suffixes when applicable.
When the distribution metric toggle is `req`, the center value and legend values SHALL show compact request formatting with up to two decimal places and `K`, `M`, or `B` suffixes when applicable.
Each distribution legend SHALL keep at most four rows visible before vertical scrolling, and any overflow scrollbar SHALL remain visually hidden while preserving scroll interaction.
Hovering a donut slice SHALL highlight the matching legend row, and hovering a legend row SHALL highlight the matching donut slice.

#### Scenario: Distribution donuts follow the active metric
- **WHEN** report data includes model and user-agent distribution rows
- **THEN** `/reports` renders both `Distribution by Model` and `Distribution by UserAgent`
- **AND** each donut center shows `Total` on one line and the active metric total on the next line
- **AND** switching a donut card from `cost` to `req` updates that card's center and legend values from compact USD totals to compact request totals

#### Scenario: Distribution donuts sync hover state with a scrollable legend
- **WHEN** report data includes more than four model or user-agent distribution rows
- **THEN** each distribution legend shows four visible rows before vertical scrolling the remainder
- **AND** the scrollbar stays visually hidden while scrolling still works
- **AND** hovering any donut slice highlights the matching legend row
- **AND** hovering any legend row highlights the matching donut slice

### Requirement: Dashboard accounts section shows account availability summary

The dashboard `Accounts` section SHALL render a compact summary derived from the existing dashboard overview accounts collection. The summary SHALL show the total registered account count, the active account count, and the unavailable account count.

An account SHALL count as active only when its dashboard status normalizes to `active`. Accounts whose normalized status is `paused`, `limited`, `exceeded`, `reauth`, or `deactivated` SHALL count as unavailable.

The summary SHALL render in the `Accounts` section header row and SHALL use the project's existing foreground, muted, positive, and negative theme color conventions for light and dark mode.

#### Scenario: Mixed account states show registered, active, and unavailable counts

- **WHEN** `GET /api/dashboard/overview` returns three accounts with statuses `active`, `paused`, and `rate_limited`
- **THEN** the dashboard `Accounts` section header shows `3 registered`
- **AND** shows `1 active`
- **AND** shows `2 unavailable`

#### Scenario: Only normalized active accounts count as active

- **WHEN** `GET /api/dashboard/overview` returns accounts with statuses `active`, `quota_exceeded`, `reauth_required`, and `deactivated`
- **THEN** only the `active` account contributes to the active count
- **AND** the other three accounts contribute to the unavailable count

#### Scenario: Theme-aware colors match dashboard conventions

- **WHEN** the dashboard renders in light mode or dark mode
- **THEN** the registered count uses foreground styling
- **AND** the labels use muted-foreground styling
- **AND** the active count uses the dashboard positive green styling
- **AND** the unavailable count uses the dashboard negative red styling

### Requirement: Dashboard overview summary cards show previous-window usage deltas

The dashboard overview API SHALL expose previous-window comparison data for the existing `Requests`, `Tokens`, and `Est. API Cost` summary cards returned by `GET /api/dashboard/overview`. The comparison SHALL be tied to the selected overview timeframe so that `1d` compares the current 1-day window with the immediately preceding 1-day window, `7d` compares the current 7-day window with the immediately preceding 7-day window, and `30d` compares the current 30-day window with the immediately preceding 30-day window.

The overview response SHALL include a comparison block that exposes whether previous-window comparison is allowed and the previous-window totals for requests, tokens, and estimated API cost. The dashboard SHALL use that block to render a compact percentage-change indicator on the existing `Requests`, `Tokens`, and `Est. API Cost` cards only. The dashboard MUST NOT add this indicator to `Error rate` or `Account burn projection`.

If the immediately preceding window is not fully covered by eligible request-log history for the selected timeframe, the overview response SHALL mark the comparison as unavailable and the dashboard SHALL hide the percentage-change indicator for those cards.

If previous-window comparison is available and the previous total for a card is greater than zero, the dashboard SHALL calculate the displayed change from the current total relative to the previous total, SHALL show increases with an upward indicator using the project's positive `emerald` styling, and SHALL show decreases with a downward indicator using the project's negative `red` styling.

#### Scenario: Daily overview renders increase from previous window

- **WHEN** `GET /api/dashboard/overview?timeframe=1d` returns current totals for requests, tokens, and estimated API cost plus comparison data with `canCompare: true`
- **AND** the previous-window totals are lower than the current-window totals
- **THEN** the dashboard renders percentage-change indicators on the `Requests`, `Tokens`, and `Est. API Cost` cards
- **AND** each increase uses an upward indicator with positive `emerald` styling

#### Scenario: Weekly overview renders decrease from previous window

- **WHEN** `GET /api/dashboard/overview?timeframe=7d` returns comparison data with `canCompare: true`
- **AND** at least one of the previous-window totals for requests, tokens, or estimated API cost is higher than the current-window total for that same card
- **THEN** the dashboard renders a downward percentage-change indicator for that card
- **AND** that decrease uses negative `red` styling

#### Scenario: Partial previous window suppresses comparison

- **WHEN** `GET /api/dashboard/overview?timeframe=7d` or `GET /api/dashboard/overview?timeframe=30d` cannot prove the immediately preceding same-length window is fully covered by eligible request-log history
- **THEN** the overview response marks the comparison as unavailable
- **AND** the dashboard does not render percentage-change indicators on the `Requests`, `Tokens`, or `Est. API Cost` cards

#### Scenario: Non-comparison cards remain unchanged

- **WHEN** the dashboard renders overview cards from `GET /api/dashboard/overview` with or without comparison data
- **THEN** `Error rate` and `Account burn projection` do not render previous-window percentage-change indicators

### Requirement: Dashboard estimated cost card meta avoids duplicate estimate and cache copy

The dashboard overview `Est. API Cost` summary card SHALL render its meta text as only the averaged cost for the selected overview timeframe. The meta text MUST NOT append duplicate estimate wording or cached-token counts.

#### Scenario: Weekly estimated cost card shows only average-per-day text

- **WHEN** `GET /api/dashboard/overview?timeframe=7d` returns an `Est. API Cost` total and the summary metrics also include cached input tokens
- **THEN** the dashboard renders the cost-card meta text as `Avg/day <currency value>`
- **AND** the same meta text does not include `API estimate`
- **AND** the same meta text does not include `cached`

#### Scenario: Daily estimated cost card shows only average-per-hour text

- **WHEN** `GET /api/dashboard/overview?timeframe=1d` returns an `Est. API Cost` total
- **THEN** the dashboard renders the cost-card meta text as `Avg/hr <currency value>`
- **AND** the same meta text does not include any extra suffix text

### Requirement: Upstream proxy admin creation flows use modal dialogs

The Settings upstream proxy section SHALL present endpoint creation, pool creation, and
pool-member addition as modal dialogs opened from explicit trigger buttons. The creation form
fields (endpoint name/scheme/host/port/credentials, pool name/member selection, pool-member
pool/endpoint selectors) SHALL NOT be rendered in the always-visible Settings layout; they
SHALL only mount when their dialog is open. Submitting a creation dialog SHALL call the existing
upstream proxy admin mutation, refresh the displayed admin state, and close the dialog on success;
a failed submission SHALL keep the dialog open so the operator can retry.

#### Scenario: Creation forms are hidden until a dialog opens

- **WHEN** an operator views the Settings page upstream proxy section
- **THEN** no endpoint, pool, or pool-member creation input fields are present in the document
- **AND** the section shows trigger buttons for adding an endpoint, creating a pool, and adding a pool member

#### Scenario: Operator creates a pool from a dialog

- **GIVEN** the upstream proxy admin API returns at least one endpoint
- **WHEN** an operator opens the create-pool dialog, names the pool, selects endpoint members, and submits
- **THEN** the dashboard calls the pool creation API with the selected endpoint ids
- **AND** refreshes the displayed upstream proxy admin state
- **AND** closes the dialog

#### Scenario: Failed creation keeps the dialog open

- **WHEN** a creation dialog submission rejects with an error
- **THEN** the dialog remains open
- **AND** the entered values are preserved so the operator can retry

### Requirement: Upstream proxy admin section summarizes configured endpoints and pools

The always-visible Settings upstream proxy section SHALL render a summary/management view that
shows the routing-enabled toggle, the default-pool selector, and readable lists of the configured
endpoints and pools (including each pool's active state and endpoint count). When no endpoints or
no pools are configured, the section SHALL show an explicit empty state for that list rather than
a blank region.

#### Scenario: Configured endpoints and pools are listed

- **WHEN** the upstream proxy admin state includes endpoints and pools
- **THEN** the section lists each endpoint with its scheme, host, and port
- **AND** lists each pool with its active state and endpoint count

#### Scenario: Empty proxy configuration shows an empty state

- **WHEN** the upstream proxy admin state has no endpoints and no pools
- **THEN** the section shows an explicit empty-state message for endpoints and for pools

### Requirement: Account routing and proxy-binding controls size predictably

The account detail routing-policy selector and the account proxy-pool selector SHALL size
themselves responsively within their container instead of using an arbitrary fixed pixel width,
and SHALL truncate long option labels gracefully rather than overflowing their container or
collapsing below a usable minimum width.

#### Scenario: Routing-policy select fills its control row

- **WHEN** the account detail panel renders the routing-policy selector
- **THEN** the selector trigger constrains its width to its container with a usable minimum
- **AND** does not hardcode a fixed `w-44` width

#### Scenario: Long proxy-pool name is truncated

- **WHEN** the account proxy-pool selector renders a pool whose name is longer than the trigger width
- **THEN** the selected label is truncated with an ellipsis within the trigger
- **AND** the selector does not overflow its container

### Requirement: Account proxy binding can test the selected pool

The account detail proxy-binding panel SHALL expose an in-place test action for the selected proxy
pool when that pool has at least one endpoint. Activating the action SHALL call the existing upstream
proxy endpoint-test API for the selected pool's first endpoint and SHALL display the latest bounded
reachability result without showing proxy credentials or account tokens.

#### Scenario: Operator tests the selected account proxy pool

- **GIVEN** an account proxy binding panel has a selected pool with at least one endpoint
- **WHEN** an operator activates the pool test action
- **THEN** the dashboard calls the existing endpoint-test API with the selected pool's first endpoint id
- **AND** displays whether the endpoint was reachable, plus bounded status/latency details when provided

#### Scenario: Pool test is disabled when no endpoint is available

- **WHEN** the selected account proxy pool has no endpoints
- **THEN** the pool test action is disabled

### Requirement: Account list presents a single add-account entry point with a chooser dialog

The account list SHALL present account creation through a single dashed-border placeholder control
rendered at the bottom of the list, instead of separate always-visible "Import" and "Add Account"
buttons. Activating the placeholder SHALL open a modal chooser dialog offering two options: adding
an account via OAuth and importing an exported `auth.json` file. Selecting an option SHALL close the
chooser and open the corresponding existing flow (the OAuth dialog or the import dialog) via the
existing handlers, without changing those flows' behavior.

#### Scenario: Add-account placeholder opens the chooser

- **WHEN** an operator views the account list
- **THEN** a single "Add account" placeholder control is shown at the bottom of the list
- **AND** no separate always-visible "Import" or "Add Account" buttons are present
- **WHEN** the operator activates the placeholder
- **THEN** a chooser dialog opens offering an "Add account" (OAuth) option and an "Import" option

#### Scenario: Choosing an option opens its existing flow

- **GIVEN** the add-account chooser dialog is open
- **WHEN** the operator selects the "Add account" option
- **THEN** the chooser closes and the existing OAuth sign-in dialog opens
- **WHEN** the operator instead selects the "Import" option
- **THEN** the chooser closes and the existing `auth.json` import dialog opens

### Requirement: Account list status filter shares the help row

The account list search input SHALL span the full width of the list controls, and the account
status filter SHALL be positioned on the same row as the "Need help?" toggle rather than beside the
search input.

#### Scenario: Status filter renders on the help row

- **WHEN** an operator views the account list
- **THEN** the search input occupies the full width of the controls row
- **AND** the account status filter control is rendered on the same row as the "Need help?" toggle

### Requirement: Account alias is edited inline from the detail header

The account detail header SHALL display the account's local label (the alias when set, otherwise the
display name or email) next to an edit (pencil) control, and SHALL NOT render a separate always-visible
alias form. Activating the edit control SHALL replace the label with an inline text input pre-filled
with the current alias plus confirm and cancel controls. Confirming SHALL persist the alias via the
existing alias handler (an empty value clears the alias) and return to the display state; cancelling
SHALL discard the edit without a network call. When an alias is set, the header SHALL still surface the
account email as a subtitle so the underlying account remains identifiable.

#### Scenario: Pencil reveals the inline alias editor

- **WHEN** an operator views the account detail header
- **THEN** the account local label is shown next to an "Edit alias" control
- **AND** no separate "Account alias" form card is rendered
- **WHEN** the operator activates the "Edit alias" control
- **THEN** the label is replaced by a text input pre-filled with the current alias, with save and cancel controls

#### Scenario: Saving and clearing the alias inline

- **GIVEN** the inline alias editor is open
- **WHEN** the operator enters a label and confirms
- **THEN** the alias is persisted via the existing alias handler and the header returns to the display state
- **WHEN** the operator clears the input and confirms
- **THEN** the alias is cleared via the existing alias handler

#### Scenario: Cancelling discards the edit

- **GIVEN** the inline alias editor is open with unsaved changes
- **WHEN** the operator cancels
- **THEN** the editor closes without calling the alias handler
- **AND** the displayed label is unchanged

### Requirement: Dashboard accounts section supports card and list views

The Dashboard Accounts section SHALL allow operators to choose between the existing card layout and a compact list layout. The default mode SHALL remain cards. The selected account view mode SHALL persist locally and apply on later dashboard visits.

The list layout SHALL use the same dashboard overview account collection as the card layout and SHALL expose account identity, status, plan, quota remaining, credits, limit warm-up state, and the same account actions available from the card layout. The list quota cells SHALL include compact visual meters for each rendered quota row while preserving numeric percent and reset timing text. The Account, Status, Plan, Quota, Credits, and Warm-up list headers SHALL be clickable sort controls. The selected list sort column and direction SHALL persist locally and apply on later dashboard visits that render the compact list layout.

#### Scenario: Dashboard defaults to card view

- **WHEN** the account view-mode preference is unset
- **THEN** the Dashboard Accounts section renders account cards
- **AND** the card/list control indicates card mode is selected

#### Scenario: Operator switches to list view

- **WHEN** an operator selects list mode in the Dashboard Accounts section
- **THEN** the account cards are replaced by a compact list of the same accounts
- **AND** the list exposes each account's status, quota, credits, warm-up state, and available actions
- **AND** each quota row includes a compact visual remaining-capacity meter

#### Scenario: Operator sorts account list columns

- **WHEN** an operator clicks a sortable list header
- **THEN** the account list sorts by that column in ascending order
- **AND** clicking the same header again toggles the sort direction
- **AND** the active sort header exposes its sort direction to assistive technology

#### Scenario: Account list sort persists locally

- **WHEN** an operator sorts the compact account list by a column and direction
- **AND** later returns to the dashboard in the same browser profile with list mode selected
- **THEN** the compact account list renders with the same active sort column and direction

#### Scenario: Account view mode persists locally

- **WHEN** an operator selects list mode
- **AND** later returns to the dashboard in the same browser profile
- **THEN** the Dashboard Accounts section renders in list mode without requiring another selection

### Requirement: Reports page exposes a visible user-agent filter

The dashboard SHALL render `/reports` with a visible `UserAgent` filter beside the existing `Model` filter. The `UserAgent` filter SHALL be single-select, SHALL use normalized `request_logs.useragent_group` values for its choices, and SHALL filter the reports payload by sending `useragent_group` on `GET /api/reports` requests.

#### Scenario: Reports page shows the user-agent filter
- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page exposes a visible `UserAgent` filter beside `Model`

#### Scenario: Reports page requests filtered data by normalized user-agent group
- **WHEN** an authenticated operator selects a `UserAgent` value on `/reports`
- **THEN** the page refetches `GET /api/reports` with `useragent_group` set to the selected normalized `request_logs.useragent_group` value

#### Scenario: Reports page reuses the relaxed reports query for user-agent filter choices
- **WHEN** `/reports` loads or refreshes filter choices
- **THEN** the page obtains `UserAgent` filter options from the same relaxed `GET /api/reports` query flow used for report filter-option discovery
- **AND** the page does not require a separate endpoint to load `UserAgent` choices

#### Scenario: Reports page shows one shared relaxed-catalog error for report filter choices
- **WHEN** the relaxed `GET /api/reports` query for report filter-option discovery fails
- **THEN** the page shows one page-owned error describing the combined `Model` and `UserAgent` option loading failure
- **AND** the page does not show separate duplicate relaxed-catalog errors for `Model` and `UserAgent`

### Requirement: Reports page renders a user-agent distribution card

The dashboard SHALL render `/reports` with a `Distribution by UserAgent` card placed below `Distribution by Model`, using aggregated `request_logs.useragent_group` values from `GET /api/reports`.

#### Scenario: Reports page shows user-agent distribution data
- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page renders `Distribution by UserAgent` below `Distribution by Model`

### Requirement: Reports distribution cards toggle between cost and requests

The dashboard SHALL render both `/reports` distribution cards with an upper-right `cost` / `req` toggle that defaults to `cost` and changes the donut slices, percentages, and legend values to match the selected metric. The `Distribution by Model` and `Distribution by UserAgent` donuts SHALL NOT render hover tooltips.

#### Scenario: Distribution cards default to cost mode
- **WHEN** an authenticated operator opens `/reports`
- **THEN** both distribution cards render in `cost` mode by default

#### Scenario: Distribution cards can switch to request mode
- **WHEN** an authenticated operator activates `req` on either distribution card
- **THEN** that card renders request-count slices, request-count values, and request-based percentages

### Requirement: Reports user-agent distribution preserves unknown buckets without collisions

`GET /api/reports` SHALL aggregate request-log rows whose normalized `request_logs.useragent_group` is `null` into a `byUseragent` bucket labeled `Missing User-Agent`. Real normalized `request_logs.useragent_group = "Unknown"` rows SHALL remain in a separate `Unknown` bucket. When `/reports` or `GET /api/reports` is filtered with `useragent_group=Missing User-Agent`, the system SHALL match those same null-backed rows, while `useragent_group=Unknown` SHALL match only real `"Unknown"` rows. The `/reports` `Distribution by UserAgent` card SHALL render the `Missing User-Agent` bucket with a fixed gray legend marker and slice color instead of a rotated palette color.

#### Scenario: Reports payload includes missing and real Unknown user-agent traffic

- **WHEN** `GET /api/reports` aggregates request logs that include one or more rows with `request_logs.useragent_group = null`
- **AND** one or more rows with normalized `request_logs.useragent_group = "Unknown"`
- **THEN** the response `byUseragent` array includes an entry with `useragent: "Missing User-Agent"`
- **AND** that entry aggregates only the null-backed rows' request counts and costs
- **AND** the response separately includes an entry with `useragent: "Unknown"` for the real normalized `"Unknown"` rows

#### Scenario: Reports filters distinguish missing and real Unknown user-agent traffic

- **WHEN** `/reports` or `GET /api/reports` requests `useragent_group=Missing User-Agent`
- **THEN** the returned report aggregates include only rows whose normalized `request_logs.useragent_group` is `null`
- **WHEN** `/reports` or `GET /api/reports` requests `useragent_group=Unknown`
- **THEN** the returned report aggregates include only rows whose normalized `request_logs.useragent_group` is the real string `"Unknown"`

#### Scenario: Reports page renders the missing user-agent bucket with fixed gray styling

- **WHEN** `/reports` renders `Distribution by UserAgent` data that includes `useragent: "Missing User-Agent"`
- **THEN** the `Missing User-Agent` legend dot uses a fixed gray color
- **AND** the matching donut slice uses that same fixed gray color

### Requirement: Reports daily charts fill missing selected days with zero-value rows

The dashboard SHALL render `/reports` `Cost by Day` and `Tokens by Day` charts from a continuous daily series covering every selected day from the current `startDate` through `endDate`. When `GET /api/reports` omits a selected date, the page SHALL insert a zero-value daily row for that date before rendering both charts.

#### Scenario: Missing API dates render as zero-value chart points

- **WHEN** an authenticated operator views `/reports` for a selected date range and the `daily` response omits one or more selected dates
- **THEN** the `Cost by Day` chart includes a point for every selected day from `startDate` through `endDate`
- **AND** each omitted date renders with `costUsd = 0`
- **AND** the `Tokens by Day` chart includes a point for every selected day from `startDate` through `endDate`
- **AND** each omitted date renders with `inputTokens = 0`, `outputTokens = 0`, `cachedInputTokens = 0`, `requests = 0`, `activeAccounts = 0`, and `errorCount = 0`

### Requirement: Daily Breakdown supports explicit visible-column sorting

The dashboard SHALL render `/reports` `Daily Breakdown` with sortable visible columns for `Day`, `Reqs`, `Input Tokens`, `Output Tokens`, `Cost`, and `Accounts`. The default sort SHALL be `Day` descending.

#### Scenario: Daily Breakdown defaults to newest day first

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the `Daily Breakdown` rows are ordered by `Day` descending by default

#### Scenario: Daily Breakdown toggles sorting for a visible column

- **WHEN** an authenticated operator activates any `Daily Breakdown` visible-column header
- **THEN** the table sorts by that column
- **AND** activating the same header again toggles the sort direction between ascending and descending

### Requirement: Reports Tokens summary subtitle shows cached totals

The dashboard SHALL render the `/reports` `Tokens` summary-card subtitle as `Input <value> · Cache <value> · Output <value>` using the current report summary totals for input, cached input, and output tokens.

#### Scenario: Tokens subtitle includes cached total

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the `Tokens` summary card subtitle includes formatted `Input`, `Cache`, and `Output` token totals in that order

### Requirement: Daily Breakdown shows cached input tokens inline

The dashboard SHALL render `/reports` `Daily Breakdown` `Input Tokens` cells as the input-token total followed by the cached input-token total in parentheses using muted secondary text.

#### Scenario: Input Tokens cell shows cached input token count

- **WHEN** a `Daily Breakdown` row has non-zero `inputTokens` and `cachedInputTokens`
- **THEN** the `Input Tokens` cell renders `<formatted inputTokens> (<formatted cachedInputTokens>)`

#### Scenario: Input Tokens cell shows zero cached tokens explicitly

- **WHEN** a `Daily Breakdown` row has `cachedInputTokens = 0`
- **THEN** the `Input Tokens` cell renders the primary input-token value followed by `(0)`
- **AND** if `inputTokens = 0` the full rendered value is `0 (0)`

### Requirement: Daily Breakdown CSV export stays chronological

The dashboard SHALL export `/reports` `Daily Breakdown` CSV rows in ascending `Day` order regardless of the current visible table sort key or direction.

#### Scenario: CSV export ignores visible descending day sort

- **WHEN** an authenticated operator exports the `Daily Breakdown` CSV while the visible table is sorted newest-first
- **THEN** the CSV rows are written from the earliest day to the latest day

#### Scenario: CSV export ignores non-day visible sort

- **WHEN** an authenticated operator exports the `Daily Breakdown` CSV while the visible table is sorted by another column
- **THEN** the CSV rows are still written from the earliest day to the latest day

### Requirement: Daily Breakdown sortable headers show visible sort state

The dashboard SHALL render a visible sort icon on every sortable `/reports` `Daily Breakdown` header. Inactive sortable headers SHALL show a muted gray unsorted indicator, and the active sorted header SHALL show a bright directional indicator matching the current ascending or descending sort direction.

#### Scenario: Inactive sortable headers show unsorted indicator

- **WHEN** an authenticated operator views `/reports`
- **THEN** each sortable `Daily Breakdown` header shows a visible unsorted icon when that column is not the active sort column

#### Scenario: Active sortable header shows ascending indicator

- **WHEN** an authenticated operator activates a `Daily Breakdown` header and the table sort is ascending for that column
- **THEN** that header shows the active ascending sort icon instead of the muted unsorted icon

#### Scenario: Active sortable header shows descending indicator

- **WHEN** an authenticated operator activates the same `Daily Breakdown` header again and the table sort becomes descending
- **THEN** that header shows the active descending sort icon instead of the muted unsorted icon

### Requirement: Dashboard supports runtime locale selection

The dashboard SHALL load translations through `i18next` + `react-i18next`, support at least `en` (default) and `zh-CN` locales, persist the user's selection in `localStorage` under the key `codex-lb-language`, and apply the active locale to the document's `lang` attribute. When no persisted preference exists, the dashboard SHALL detect the browser language and use `zh-CN` for any `zh*` tag and `en` otherwise.

#### Scenario: First visit with a Chinese browser

- **WHEN** a user opens the dashboard for the first time with `navigator.language = "zh-CN"` and no persisted preference
- **THEN** the in-scope surface (header, status-bar labels, auth screens) renders in Simplified Chinese
- **AND** `localStorage` contains `codex-lb-language=zh-CN`

#### Scenario: First visit with an unsupported browser language

- **WHEN** a user opens the dashboard for the first time with `navigator.language` set to anything that does not start with `zh`
- **THEN** the in-scope surface renders in English
- **AND** the dashboard does not raise locale-loading errors

#### Scenario: User toggles the language

- **WHEN** the user activates the language switcher in the app header and selects `简体中文`
- **THEN** the in-scope surface re-renders in Simplified Chinese without a full page reload
- **AND** `localStorage.codex-lb-language` is set to `zh-CN`
- **AND** `document.documentElement.lang` is set to `zh-CN`

#### Scenario: Selection persists across reloads

- **WHEN** the user reloads the dashboard after selecting a language
- **THEN** the previously selected language is reapplied before the first paint

### Requirement: Settings page renders in the active locale

The Settings page and its constituent sections (`Appearance`, `Routing`, `Import`, `Session`, `Password`, `TOTP`) SHALL render all user-visible strings — page title and subtitle, section headings and descriptions, switch and select labels, button labels, dialog titles and descriptions, validation messages, warning banners, and toast messages — through the active i18n locale. Selecting `zh-CN` MUST translate the entire surface above to Simplified Chinese, with the explicit exceptions of the embedded `ApiKeysSection`, `FirewallSection`, `QuotaPlannerSection`, `StickySessionsSection`, and `UpstreamProxySettings` rendered inside Settings, which belong to other capabilities and remain English until their own migrations.

#### Scenario: Switching to Simplified Chinese translates the Settings page

- **WHEN** a user opens the Settings page with `zh-CN` selected as the active language
- **THEN** the page heading reads `设置`
- **AND** every section heading inside the page (`外观`, `路由`, `导入`, `会话`, `密码`, `TOTP`) renders in Simplified Chinese
- **AND** every dialog opened from the page (`Set password`, `Change password`, `Remove password`, `Verify password`, `Enable TOTP`, `Disable TOTP`) renders its title, description, field labels, and submit button in Simplified Chinese

#### Scenario: Session lifetime invalid-input warning preserves the inline example

- **WHEN** a user enters a non-integer hour value such as `1.5` into the dashboard session lifetime input
- **THEN** the inline warning renders with `1.5` wrapped in `<code>` regardless of the active locale
- **AND** the surrounding sentence is translated according to the active locale

#### Scenario: TOTP code-length validation respects the active locale

- **WHEN** a user submits the Enable TOTP or Disable TOTP form with a code shorter than 6 characters and the active locale is `zh-CN`
- **THEN** the form-level validation message renders as `请输入 6 位验证码`
- **AND** the same submission with `en` active renders as `Enter a 6-digit code`

### Requirement: Dashboard request details expose client IP

The dashboard request-log API response MUST expose the persisted `clientIp` value when present. The Request Details dialog MUST render `Client IP` with the full value when present, MUST allow copying the value, and MUST render `—` when no client IP is stored.

#### Scenario: Request details show client IP

- **WHEN** a request log entry has `clientIp: "203.0.113.7"`
- **THEN** the Request Details dialog renders `Client IP` with value `203.0.113.7`
- **AND** the value can be copied

#### Scenario: Request details show missing client IP

- **WHEN** a request log entry has `clientIp: null`
- **THEN** the Request Details dialog renders `Client IP` with value `—`

### Requirement: Account usage panel supports confirmed usage reset

The Accounts page selected-account Usage panel SHALL expose a Reset action
inside the Usage resets row when reset-credit availability is shown. The action
SHALL require operator confirmation, SHALL consume one upstream usage reset
credit for the selected account, SHALL force-fetch upstream usage after a
successful or idempotently successful consume without sending model probe
traffic, and SHALL refresh account-related dashboard queries after success. The
dashboard SHALL NOT reduce or add permanent polling intervals to make this
reset appear sooner.

#### Scenario: Confirmed account usage reset consumes one credit

- **GIVEN** an active selected account is visible on the Accounts page
- **AND** the selected account has at least one available usage reset credit
- **WHEN** the operator clicks the Usage panel Reset action
- **AND** confirms the dialog
- **THEN** the dashboard sends a usage reset consume request for the selected account
- **AND** codex-lb does not send a model probe request
- **AND** account-related usage, trend, reset-credit, and dashboard summary
  queries are invalidated after success
- **AND** no reset-credit availability query is configured with a permanent
  refetch interval

#### Scenario: Dismissed account usage reset does not consume a credit

- **GIVEN** an active selected account is visible on the Accounts page
- **WHEN** the operator clicks the Usage panel Reset action
- **AND** cancels the dialog
- **THEN** the dashboard does not send a usage reset consume request

### Requirement: Automations page is available from top-level navigation

The SPA MUST expose an `Automations` top-level navigation item that routes to `/automations`.

#### Scenario: Open Automations page from header

- **WHEN** a signed-in user selects `Automations` in the header navigation
- **THEN** the SPA navigates to `/automations`
- **AND** the app requests the automation job list from `/api/automations`

### Requirement: Automations page supports job lifecycle actions

The `Automations` page MUST let operators create, edit, enable/disable, delete, and run jobs, including selecting accounts and model.

#### Scenario: Create job in GUI

- **WHEN** a user creates a daily ping automation with schedule, model, and account set
- **THEN** the SPA submits `POST /api/automations`
- **AND** the new job appears in the jobs table with `nextRunAt`

#### Scenario: Toggle enablement

- **WHEN** a user toggles a job off or on from the jobs table
- **THEN** the SPA submits `PATCH /api/automations/{id}`
- **AND** the table reflects the updated enabled state

#### Scenario: Trigger manual run

- **WHEN** a user selects `Run now` for a job
- **THEN** the SPA submits `POST /api/automations/{id}/run-now`
- **AND** latest run status updates in the jobs table

### Requirement: Automations page surfaces run failures

The UI MUST present recent run outcomes and show failure details to the user.

#### Scenario: Inspect failed run

- **WHEN** a user opens run history for a job with a failed run
- **THEN** the UI shows run status, timestamps, and failure details (`errorCode`, `errorMessage`)
- **AND** the jobs table highlights the latest failed state

### Requirement: Automations form validates required scheduling inputs

The Automations create/edit form MUST prevent submission when required fields are missing or invalid.

#### Scenario: All-accounts selection is allowed

- **WHEN** a user leaves `accountIds` empty in the create/edit form
- **THEN** the SPA treats the selection as `All accounts`
- **AND** submit remains allowed when other required fields are valid

#### Scenario: Block submit when there are no available accounts

- **WHEN** the accounts catalog is empty
- **THEN** the SPA blocks submit and shows a validation message

#### Scenario: Block submit when schedule time or timezone is invalid

- **WHEN** a user provides an invalid schedule time or timezone value
- **THEN** the SPA blocks submit and shows a validation message

### Requirement: Automations page accepts extended reasoning efforts

The Automations page SHALL allow operators to create and update scheduled
refresh jobs using any reasoning effort advertised by the selected model,
including extended GPT-5.6 efforts such as `max` and `ultra`.

#### Scenario: Automation dialog offers extended model reasoning efforts

- **WHEN** a selected model advertises `max` or `ultra` in `supportedReasoningEfforts`
- **THEN** the automation create/edit dialog offers those efforts as selectable values

### Requirement: Accounts list uses available tall-viewport space

The Accounts page MUST size its scrollable account rows from the available
viewport height without imposing a smaller fixed height ceiling. The bound MUST
leave the page controls and fixed status bar visible, so account rows cannot
extend below the viewport even when the selected-account detail panel makes the
page taller. Optional controls MUST consume space from that bound according to
their rendered height. The search, filter, sort, help, and Add account controls
MUST remain outside the rows scroll region, and a list longer than the available
region MUST continue to scroll internally. When the controls and rows require
less height than the selected account details, the left card MUST remain
content-sized instead of stretching an empty bordered area to the bottom of the
details column.

#### Scenario: Tall desktop viewport expands the rows region

- **WHEN** the Accounts page renders a long account list in a 1200px-tall desktop viewport
- **THEN** the account rows region is taller than 32rem
- **AND** the region uses the otherwise-empty space beneath the list controls
- **AND** the final visible account row region ends above the fixed status bar

#### Scenario: Expanded help panel consumes rows space

- **WHEN** a user expands Windows OAuth Help above a long account list in a 1200px-tall desktop viewport
- **THEN** the help panel remains visible outside the rows scroll region
- **AND** the rows region shrinks by the rendered help-panel height
- **AND** the rows region still ends above the fixed status bar
- **AND** the final account remains reachable through internal scrolling

#### Scenario: Shorter account list does not stretch its card

- **WHEN** all account rows fit within the viewport-aware region
- **AND** the selected-account details are taller than the list controls and rows
- **THEN** the left card ends after the account rows and its normal bottom padding
- **AND** it does not render a large empty bordered area beneath the final account

#### Scenario: Account pool still exceeds the available height

- **WHEN** the account rows require more space than the viewport-aware region provides
- **THEN** the rows remain internally scrollable through the final account
- **AND** the Add account action remains visible outside the scroll region

### Requirement: Account management page supports account import and OAuth add flows

The Accounts page SHALL support account import, untargeted OAuth account
addition, and targeted OAuth reauthentication. Reauthentication MUST preserve
separate local seats that share one workspace `chatgpt_account_id`.

#### Scenario: Account import

- **WHEN** a user opens the import flow and uploads an auth.json file
- **THEN** the app calls `POST /api/accounts/import` and refreshes the account list on success

#### Scenario: OAuth add account

- **WHEN** a user clicks the add account button
- **THEN** an OAuth dialog opens with browser and device code flow options
- **AND** the OAuth start request does not target an existing local account

#### Scenario: Reauthentication targets the selected local seat

- **GIVEN** two local Team seats share one upstream `chatgpt_account_id`
- **AND** each seat has a distinct `chatgpt_user_id`
- **WHEN** an operator starts reauthentication from one selected account row
- **THEN** the selected local account ID is retained in server-side OAuth flow state
- **AND** successful OAuth replaces credentials only on that selected row

#### Scenario: Wrong browser seat is rejected

- **GIVEN** reauthentication targets seat A
- **WHEN** OAuth returns seat B from the same Team workspace
- **THEN** the flow fails without writing seat B's credentials to seat A
- **AND** neither local account row is merged or deleted

#### Scenario: Token refresh preserves seat identity

- **WHEN** a refresh response contains a stable user principal
- **THEN** the service persists that principal as `chatgpt_user_id`
- **AND** continues using `chatgpt_account_id` as the upstream workspace identity

### Requirement: Dashboard tolerates browser translation DOM mutation

The dashboard HTML shell SHALL allow browser/extension translation while protecting React reconciliation from external DOM node moves.

#### Scenario: Dashboard permits browser translation

- **WHEN** the browser loads the dashboard HTML shell
- **THEN** the document, body, and React root do not opt out of browser translation

#### Scenario: Dashboard tolerates externally moved React nodes

- **WHEN** an extension moves a React-owned DOM node before React removes or inserts around it
- **THEN** the dashboard startup guard logs the external mutation
- **AND** the guarded DOM operation returns without throwing a reconciliation-stopping exception

### Requirement: Reports page sends browser-local timezone context

The `/reports` page SHALL detect the browser's current IANA timezone, cache the latest detected valid value locally for convenience, and include a valid timezone in `GET /api/reports` requests whenever one is available. The page SHALL prefer the browser's current valid timezone over any cached value, SHALL reuse the cached valid timezone when live detection is unavailable or invalid, and SHALL omit the `timezone` query parameter only when neither the live nor cached value is valid.

#### Scenario: Reports page includes browser timezone on requests

- **WHEN** an authenticated operator opens `/reports` or changes a report filter
- **THEN** the request to `GET /api/reports` includes the browser's current IANA timezone in the `timezone` query parameter when detection succeeds

#### Scenario: Reports page reuses cached timezone when live detection fails

- **WHEN** the browser cannot provide a valid IANA timezone name
- **AND** the page has a cached valid timezone from an earlier successful detection
- **THEN** the reports page still requests `GET /api/reports`
- **AND** the request uses the cached valid timezone in the `timezone` query parameter

#### Scenario: Reports page omits timezone only when no valid timezone is available

- **WHEN** the browser cannot provide a valid IANA timezone name
- **AND** the page does not have a cached valid timezone
- **THEN** the reports page still requests `GET /api/reports`
- **AND** the request omits the `timezone` query parameter

### Requirement: Reports endpoint applies timezone-aware ranges and daily bucketing

`GET /api/reports` SHALL interpret `start_date` and `end_date` as calendar dates in the supplied IANA timezone, convert those local-midnight boundaries to UTC for filtering, and group `daily` rows by calendar day in that same timezone. When the timezone is missing or invalid, the endpoint MUST fall back to UTC.

#### Scenario: Reports endpoint uses local-day buckets before UTC midnight

- **WHEN** `/api/reports` receives `start_date`, `end_date`, and `timezone=America/Los_Angeles`
- **AND** a request log row falls on `2026-06-02T01:30:00Z`
- **THEN** the row is included in the `2026-06-01` daily bucket for that response

#### Scenario: Reports endpoint falls back to UTC for invalid timezone

- **WHEN** `/api/reports` receives an invalid `timezone` value
- **THEN** the endpoint still returns a successful response
- **AND** it interprets the report range and daily buckets in UTC

### Requirement: Reports summary cards show previous-window deltas conservatively

`GET /api/reports` SHALL expose a `comparison` block for the `Total Cost`, `Tokens`, and `Requests` summary cards that includes `canCompare` plus the previous-window totals for cost, tokens, and requests. The current window and previous window SHALL use equal calendar-window lengths derived from the selected report date range. The endpoint SHALL set `canCompare` to `true` only when eligible report history fully covers the immediately preceding window. When `canCompare` is `false`, the `/reports` summary cards SHALL hide the previous-window percentage indicators. Even when `canCompare` is `true`, an individual summary card SHALL hide its own percentage indicator when that card's previous-window total is zero.

#### Scenario: Reports summary cards show previous-window increase

- **WHEN** `GET /api/reports` returns current summary totals plus `comparison.canCompare: true`
- **AND** a previous-window total for `Total Cost`, `Tokens`, or `Requests` is lower than the current total for that same card
- **THEN** the matching summary card renders a visible percentage-change increase indicator

#### Scenario: Incomplete previous window suppresses comparison

- **WHEN** the earliest eligible report activity is later than the start of the immediately preceding report window
- **THEN** `GET /api/reports` returns `comparison.canCompare: false`
- **AND** the `/reports` summary cards do not render previous-window percentage indicators

#### Scenario: Zero previous total suppresses the matching card indicator

- **WHEN** `GET /api/reports` returns `comparison.canCompare: true`
- **AND** the previous-window total for one of `Total Cost`, `Tokens`, or `Requests` is `0`
- **THEN** that summary card does not render a previous-window percentage indicator
- **AND** the other summary cards may still render percentage indicators when their own previous-window totals are greater than `0`

### Requirement: Reports daily breakdown renders a continuous calendar window

The `/reports` daily breakdown table SHALL render one row per calendar day in the selected date range. Each row SHALL display its date as an ISO `yyyy-mm-dd` calendar date string. If the reports API omits one or more days inside that range, the table SHALL synthesize zero-valued rows for those days using the same row styling as API-backed rows. The table SHALL keep the header visible while only the data rows scroll, with a default visible body height of seven row heights.

#### Scenario: Daily breakdown fills missing days with zero-valued rows

- **WHEN** the selected reports window spans `2026-06-05` through `2026-06-12`
- **AND** the reports API returns daily rows for every day except `2026-06-06`
- **THEN** the daily breakdown renders a row for `2026-06-06`
- **AND** that row shows zero requests, zero input tokens, zero output tokens, zero cost, and zero accounts
- **AND** that row uses the same row styling as neighboring rows

#### Scenario: Daily breakdown header stays visible while rows scroll

- **WHEN** the daily breakdown contains more than seven rows
- **THEN** the table header remains visible
- **AND** only the table body scrolls vertically through the remaining rows

#### Scenario: Daily breakdown preserves ISO bucket dates

- **WHEN** the reports API returns a daily bucket row with `date` set to `2026-06-01`
- **THEN** the daily breakdown table renders that row label as `2026-06-01`

### Requirement: Reports daily charts use symmetric horizontal padding

The `/reports` `Cost by Day` and `Tokens by Day` charts SHALL use equal left and right horizontal plot padding within their chart cards.

#### Scenario: Daily charts render with balanced left and right inset

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the `Cost by Day` and `Tokens by Day` charts render with equal left and right horizontal padding around the plotted area

### Requirement: API keys settings expose quota privacy toggle
The Settings page SHALL include a toggle in the API Keys section that controls `hide_upstream_quota_from_api_keys`.

#### Scenario: Toggle is visible with the API keys controls

- **WHEN** the Settings page renders the API Keys section
- **THEN** the quota privacy toggle SHALL be shown alongside the API key auth toggle

#### Scenario: Toggle persists through settings save

- **WHEN** the user changes the quota privacy toggle
- **THEN** the settings update request SHALL include `hideUpstreamQuotaFromApiKeys`

### Requirement: Dashboard request-log archive lookup
The dashboard request-log detail dialog SHALL use each row's `archiveRequestId` for conversation archive lookup when that field is present. For older API responses that omit `archiveRequestId`, it SHALL fall back to the row's `requestId`.

#### Scenario: Detail dialog uses archive lookup id
- **WHEN** a request-log row has `requestId: "resp_123"` and `archiveRequestId: "req_123"`
- **AND** the operator opens the request detail dialog
- **THEN** the archive panel queries archive records for `req_123`

#### Scenario: Detail dialog remains backward compatible
- **WHEN** a request-log row does not include `archiveRequestId`
- **AND** the operator opens the request detail dialog
- **THEN** the archive panel queries archive records for the row's `requestId`

### Requirement: Dashboard manages OpenAI-compatible model sources

The Settings page SHALL provide an operator control surface for
OpenAI-compatible model sources. Operators SHALL be able to create a source with
a name, base URL, optional upstream API key, route-shape support flags, and one
or more model ids. Operators SHALL be able to enable, disable, and delete
sources. The dashboard MUST NOT expose decrypted upstream source API keys after
creation.

#### Scenario: Operator creates a vLLM model source

- **WHEN** an operator submits a model source with base URL `http://localhost:8000/v1`
- **AND** model id `local-coder`
- **THEN** the dashboard calls `POST /api/model-sources/`
- **AND** the new source appears in the Settings model-source list

#### Scenario: Operator disables a model source

- **WHEN** an operator toggles an enabled model source off
- **THEN** the dashboard calls `PATCH /api/model-sources/{sourceId}` with `isEnabled=false`
- **AND** the source remains listed as disabled

### Requirement: Dashboard model picker includes source models

The dashboard model listing endpoint (`GET /api/models`) SHALL include enabled
OpenAI-compatible source models alongside subscription registry models so
API-key model allowlists can reference source models. Duplicate slugs MUST be
listed once with the subscription entry taking precedence.

#### Scenario: Allowed-models picker offers a source model

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **WHEN** the dashboard requests `GET /api/models`
- **THEN** the response includes `local-coder`
- **AND** an API key allowlisted to `local-coder` can call it through the proxy

### Requirement: Dashboard API-key forms assign model sources

The API-key create and edit dialogs SHALL allow operators to assign zero or
more model sources separately from account assignments. Selecting no model
sources SHALL mean all eligible sources are allowed subject to model allowlists
and route compatibility.

#### Scenario: Create key scoped to a model source

- **WHEN** an operator creates an API key and selects model source `src_vllm`
- **THEN** the dashboard sends `assignedSourceIds=["src_vllm"]`
- **AND** the API key response preserves the assigned source id

#### Scenario: Edit key clears source scope

- **WHEN** an API key has assigned source ids
- **AND** an operator clears the source selection
- **THEN** the dashboard sends `assignedSourceIds=[]`

### Requirement: Accounts page exposes a reset-credits redeem action

The Accounts page per-account action bar SHALL render a `Reset (N)` button next to the existing Export button with matching button styling whenever the account reports `available_reset_credits > 0`, where `N` is the available reset-credit count for that account. The button SHALL be hidden when `available_reset_credits` is `0`. Activating the button SHALL open a confirmation dialog that describes redeeming the soonest-expiring banked reset credit for that account and, when credit details are available, shows the soonest credit's expiry in local time using `YYYY-MM-DD HH:MM:SS`. Confirming SHALL submit a redeem request for that account and refresh account data on success.

#### Scenario: Reset button mirrors Export styling and placement
- **WHEN** the Accounts page renders the per-account action bar for an account with `available_reset_credits > 0`
- **THEN** a `Reset (N)` button appears immediately next to the Export button
- **AND** the button uses the same size, variant, and class as the Export button

#### Scenario: Reset button hidden when no credits available
- **WHEN** an account reports `available_reset_credits: 0`
- **THEN** the per-account action bar renders no "Reset" button

#### Scenario: Confirmation required before redeem
- **WHEN** the operator clicks the "Reset" button
- **THEN** a confirmation dialog opens describing the soonest-expiring banked reset-credit redeem action
- **AND** no redeem request is sent until the operator confirms

#### Scenario: Confirmation dialog shows local expiry timestamp
- **WHEN** the operator opens the reset-credit confirmation dialog and credit details include an expiry timestamp
- **THEN** the dialog renders the credit expiry in local time using `YYYY-MM-DD HH:MM:SS`

### Requirement: AccountListItem displays a reset-credits count badge

The Accounts page `AccountListItem` SHALL render a count badge pinned to the right-upper radius of the item whenever the account reports `available_reset_credits > 0`. The badge SHALL display the integer count, capped visually at `"99+"` when the count exceeds 99. The badge SHALL be absent when `available_reset_credits` is `0`.

#### Scenario: Badge shows the available count
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 3`
- **THEN** a count badge pinned to the item's right-upper radius displays `3`

#### Scenario: Badge caps at 99+
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 120`
- **THEN** the count badge displays `99+`

#### Scenario: Badge absent when zero
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 0`
- **THEN** no count badge is rendered

### Requirement: Accounts page can sort by available reset credits

The Accounts page sort selector SHALL offer a "Most reset credits" option and SHALL use it as the default Accounts page ordering. That ordering SHALL sort accounts by `available_reset_credits` descending. Ties SHALL be broken by `reset_credit_nearest_expires_at` ascending (soonest expiring first), and accounts with no expiry SHALL sort after accounts that have one.

#### Scenario: More available credits sorts first
- **WHEN** the operator opens the Accounts page with the default sort mode
- **AND** account A has `available_reset_credits: 4` and account B has `available_reset_credits: 1`
- **THEN** account A appears before account B

#### Scenario: Tie breaks by soonest expiry
- **WHEN** two accounts have equal `available_reset_credits`
- **AND** one account's soonest credit expires before the other's
- **THEN** the account with the earlier `reset_credit_nearest_expires_at` appears first

### Requirement: Dashboard accounts section exposes a reset-credits redeem action

The Dashboard Accounts section SHALL render a reset action next to the existing Details action in both the table and grid views for any account with `available_reset_credits > 0`. The grid view label SHALL read `Reset (N)`. The table view MAY remain icon-only, but its tooltip/title SHALL include the available reset-credit count. The action SHALL be absent when `available_reset_credits` is `0`. Activating the action SHALL open the same confirmation flow as the Accounts page reset action.

#### Scenario: Table view shows reset next to details
- **WHEN** the Dashboard Accounts section renders in table view for an account with `available_reset_credits > 0`
- **THEN** a "Reset" action appears in the same action cell as the Details action

#### Scenario: Grid view shows reset next to details
- **WHEN** the Dashboard Accounts section renders in grid view for an account with `available_reset_credits > 0`
- **THEN** a `Reset (N)` button appears next to the Details button on the account card

#### Scenario: Reset action absent when no credits
- **WHEN** an account reports `available_reset_credits: 0`
- **THEN** the Dashboard Accounts section renders no "Reset" action for that account in either view

### Requirement: Dashboard header shows the total available reset-credit count

The dashboard top navigation SHALL render the total available reset-credit count on the Accounts tab, pinned to the tab's upper-right radius. The total SHALL equal the sum of `available_reset_credits` across the current account list data. The badge SHALL display `99+` when the total exceeds 99 and SHALL be hidden when the total is 0.

#### Scenario: Accounts tab shows the summed total
- **WHEN** the current account list totals `available_reset_credits` to `14`
- **THEN** the Accounts nav tab displays a badge with `14`

#### Scenario: Accounts tab caps large totals
- **WHEN** the current account list totals `available_reset_credits` to `120`
- **THEN** the Accounts nav tab displays a badge with `99+`

#### Scenario: Accounts tab hides empty totals
- **WHEN** every account reports `available_reset_credits: 0`
- **THEN** the Accounts nav tab displays no reset-credit badge

### Requirement: Reset actions display a single-unit expiry countdown

Every "Reset" button SHALL display a small countdown label of the soonest-expiring credit's expiry, formatted as a single time unit: `"${d}d"` for any remaining duration of one day or more, `"${h}h"` for durations under one day but at least one hour, `"${m}m"` for durations under one hour but at least one minute, and `"now"` for durations under one minute. The label SHALL render in the destructive/red color when the remaining duration is strictly less than 7 days, and in the default muted color otherwise.

#### Scenario: Days format for duration at or above one day
- **WHEN** a Reset button renders for a credit whose `expires_at` is 12 days away
- **THEN** the countdown label reads `12d`
- **AND** the label uses the default muted color

#### Scenario: Red color under seven days
- **WHEN** a Reset button renders for a credit whose `expires_at` is 6 days away
- **THEN** the countdown label reads `6d`
- **AND** the label uses the destructive/red color

#### Scenario: Hours and minutes use the smaller unit
- **WHEN** a Reset button renders for a credit whose `expires_at` is 13 hours away
- **THEN** the countdown label reads `13h`
- **AND** the label uses the destructive/red color

#### Scenario: Sub-minute duration shows now
- **WHEN** a Reset button renders for a credit whose `expires_at` is 30 seconds away
- **THEN** the countdown label reads `now`
- **AND** the label uses the destructive/red color

### Requirement: API key edit dialog

The API key edit dialog SHALL allow operators to update restrictions and
lifecycle settings without accidental dismissal from nested menu interactions.
Clicking outside the dialog SHALL still dismiss the dialog when no nested
dashboard menu surface is involved.

#### Scenario: Nested select interactions do not dismiss the edit dialog

- **WHEN** an operator opens the API key edit dialog
- **AND** chooses an item from a select, model selector, account selector,
  popover, or calendar surface rendered outside the dialog content
- **THEN** the edit dialog remains open with the selected value preserved

#### Scenario: Outside click still dismisses the edit dialog

- **WHEN** an operator clicks outside the API key edit dialog and outside any
  nested dashboard menu surface
- **THEN** the edit dialog closes

### Requirement: Reports API SHALL reject oversized daily ranges

`GET /api/reports` SHALL reject requests whose inclusive `start_date` to
`end_date` span exceeds 730 calendar days after applying endpoint defaults for
any omitted bound.

#### Scenario: Oversized report range is rejected

- **WHEN** an authenticated operator requests `/api/reports` with a date span
  longer than 730 days
- **THEN** the API returns a 400-class response
- **AND** the backend does not expand the request into per-day report buckets

#### Scenario: Single-bound report range is validated after defaults

- **WHEN** an authenticated operator requests `/api/reports` with only
  `start_date` set to a date more than 730 days before the effective end date
- **THEN** the API returns a 400-class response
- **AND** the backend does not expand the request into per-day report buckets

### Requirement: Request detail dialog displays elapsed latency
The dashboard request-log `View Details` dialog SHALL display `latency_ms` as an `Elapsed` field next to the `Plan` field. The display value SHALL use `ms` units for values under 1000 ms and `s` units (to one decimal) for values 1000 ms or greater. When `latency_ms` is null, the field SHALL render an em dash (`—`).

#### Scenario: Latency under one second shown in ms
- **WHEN** a request log detail dialog opens and the row has `latency_ms: 500`
- **THEN** the dialog displays `500 ms` in the `Elapsed` field

#### Scenario: Latency at or above one second shown in seconds
- **WHEN** a request log detail dialog opens and the row has `latency_ms: 1500`
- **THEN** the dialog displays `1.5 s` in the `Elapsed` field

#### Scenario: Missing latency renders em dash
- **WHEN** a request log detail dialog opens and the row has `latency_ms: null`
- **THEN** the dialog displays `—` in the `Elapsed` field

### Requirement: Dashboard serving is compressed, cache-correct, and chart-lazy

Dashboard API and static-asset responses MUST be served gzip-compressed when the client accepts it, while proxy paths MUST NOT pass through a compressing wrapper. Content-hashed assets under `/assets/` MUST be served with immutable year-long `Cache-Control`; `index.html` MUST remain `no-cache`. Chart vendor code MUST NOT load before first paint: it MUST live in an async-only chunk that is neither statically imported by the entry chunk nor modulepreloaded.

#### Scenario: Assets are compressed and immutable

- **WHEN** a browser requests a hashed asset under `/assets/` with `Accept-Encoding: gzip`
- **THEN** the response is gzip-encoded
- **AND** carries `Cache-Control: public, max-age=31536000, immutable`

#### Scenario: index.html stays fresh across deploys

- **WHEN** the SPA shell is requested
- **THEN** the response carries `Cache-Control: no-cache`

#### Scenario: Proxy streaming paths are never compressed by the dashboard wrapper

- **WHEN** a request targets a proxy path (`/backend-api/*`, `/v1/*`)
- **THEN** the dashboard gzip middleware passes it through untouched

#### Scenario: Ranged asset requests bypass compression

- **WHEN** an asset request carries a `Range` header
- **THEN** the response is served uncompressed with a valid 206 `Content-Range` over unencoded bytes

#### Scenario: Chart vendor code loads lazily

- **WHEN** the built dashboard entry page loads
- **THEN** the recharts chunk is not statically imported by the entry chunk and not modulepreloaded
- **AND** charts render correctly once their async chunk loads

### Requirement: Account quota displays hide expired windows

Account summary payloads SHALL present the primary (short) quota window as absent — null remaining percentage, remaining credits, reset timestamp, and window duration — when its last usage sample has an elapsed `reset_at`, instead of freezing the stale sample. Accounts without any primary sample SHALL NOT display an optimistic 100% primary remaining default. Long (weekly/monthly) window displays keep the raw samples: their consumers advance elapsed resets by design (weekly credit pace) and upstream still reports them, so staleness is transient. Displayed account status SHALL apply the same expired-window treatment routing applies before deriving the badge: an elapsed primary sample is a reset window, not exhaustion evidence, so a frozen ≥100% sample MUST NOT surface a rate-limited badge that the selector would never apply.

#### Scenario: Expired 5h sample displays as absent

- **GIVEN** upstream stopped reporting the short window and an account's last primary sample has an elapsed `reset_at`
- **WHEN** the dashboard loads account summaries
- **THEN** the account's primary window fields are null
- **AND** the UI renders the 5h quota as absent, matching the weekly-only presentation

#### Scenario: Expired exhausted sample does not display rate-limited

- **GIVEN** an active account whose last primary sample reports 100% used with an elapsed `reset_at`
- **WHEN** account summaries are built
- **THEN** the primary window fields are null
- **AND** the account status stays active instead of inferring rate-limited from the stale sample

#### Scenario: Missing primary data is not optimistic

- **WHEN** an account has no primary usage sample and is not a weekly-only plan
- **THEN** `primary_remaining_percent` is null rather than 100

#### Scenario: Live windows are unaffected

- **WHEN** an account's primary sample has an unexpired `reset_at`
- **THEN** the summary displays its used/remaining percentages, reset, and duration unchanged

