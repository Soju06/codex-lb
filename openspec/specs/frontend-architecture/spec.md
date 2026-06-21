# frontend-architecture Specification

## Purpose

Define dashboard surface contracts so settings, account management, and operational views stay coherent across the SPA.
## Requirements
### Requirement: Settings page
The Settings page SHALL include sections for: routing settings (sticky threads, reset priority, prompt-cache affinity TTL), password management (setup/change/remove), TOTP management (setup/disable), API key auth toggle, API key management (table, create, edit, delete, regenerate), and sticky-session administration.

#### Scenario: Save prompt-cache affinity TTL
- **WHEN** a user updates the prompt-cache affinity TTL from the routing settings section
- **THEN** the app calls `PUT /api/settings` with the updated TTL and reflects the saved value

#### Scenario: View sticky-session mappings
- **WHEN** a user opens the sticky-session section on the Settings page
- **THEN** the app fetches sticky-session entries and displays each mapping's kind, account, timestamps, and stale/expiry state

#### Scenario: Purge stale prompt-cache mappings
- **WHEN** a user requests a stale purge from the sticky-session section
- **THEN** the app calls the sticky-session purge API and refreshes the list afterward

### Requirement: Accounts page

The Accounts page SHALL display a two-column layout: left panel with searchable account list, import button, and add account button; right panel with selected account details including usage, token info, and actions (pause/resume/delete/re-authenticate). The browser OAuth stage SHALL show an authorization URL with a copy action that remains functional in secure and non-secure contexts.

The Accounts page SHALL also allow exporting a selected account as an OpenCode-compatible `auth.json` payload with explicit raw-token warnings.

#### Scenario: Account selection

- **WHEN** a user clicks an account in the list
- **THEN** the right panel shows the selected account's details

#### Scenario: Account import

- **WHEN** a user clicks the import button and uploads an auth.json file
- **THEN** the app calls `POST /api/accounts/import` and refreshes the account list on success

#### Scenario: OAuth add account

- **WHEN** a user clicks the add account button
- **THEN** an OAuth dialog opens with browser and device code flow options

#### Scenario: OAuth browser authorization URL copy fallback

- **WHEN** a user clicks Copy for the browser authorization URL inside the OAuth dialog
- **THEN** the copy operation succeeds using secure Clipboard API when available
- **AND** falls back to dialog-scoped `execCommand("copy")` when secure Clipboard API is unavailable or blocked

#### Scenario: OAuth browser authorization URL copy failure feedback

- **WHEN** both clipboard copy paths fail for the browser authorization URL inside the OAuth dialog
- **THEN** the dialog surfaces a visible copy failure message

#### Scenario: Device OAuth start begins polling

- **WHEN** the app starts Device Code OAuth with `POST /api/oauth/start`
- **AND** the response includes a `deviceAuthId` and `userCode`
- **THEN** the backend starts polling for the device token without requiring a separate `/api/oauth/complete` call
- **AND** a later `/api/oauth/complete` call remains safe and does not start a duplicate polling task

#### Scenario: Account actions

- **WHEN** a user clicks pause/resume/delete on an account
- **THEN** the corresponding API is called and the account list is refreshed

#### Scenario: Concurrent browser OAuth sessions stay isolated
- **WHEN** two browser PKCE OAuth sessions are started concurrently from separate dashboard tabs or operators
- **AND** each session later submits its own callback URL
- **THEN** each callback is matched against the flow that minted its `state` token
- **AND** one flow does not invalidate or overwrite the other flow's callback state

#### Scenario: Browser OAuth link refresh
- **WHEN** a user is on the browser PKCE step of the OAuth dialog
- **AND** the current authorization URL has already been used or needs to be replaced
- **THEN** the dialog offers a refresh action that starts the browser OAuth flow again without leaving the dialog
- **AND** the dialog updates to the newly generated authorization URL

#### Scenario: Export selected account from dashboard
- **WHEN** a user clicks the OpenCode export action for a selected account
- **THEN** the dashboard requests a per-account export from the backend
- **AND** shows copy/download controls for the official OpenCode `auth.json` payload
- **AND** warns that the payload contains raw account tokens
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
The Accounts page account list SHALL render a compact 5h quota row and a weekly quota row for accounts that have both quota windows, and SHALL include the time remaining until reset for each rendered row when a reset timestamp is available. Weekly-only accounts SHALL omit the 5h row.

#### Scenario: Regular account shows both quota rows
- **WHEN** the account list renders an account with both primary and weekly quota windows
- **THEN** the list item shows both 5h and weekly quota rows
- **AND** each rendered row shows its reset countdown

#### Scenario: Weekly-only account omits the 5h row
- **WHEN** the account list renders an account whose primary window is absent
- **THEN** the list item does not render a 5h quota row
- **AND** the weekly quota row still renders

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

The dashboard SHALL show weekly quota pace when account weekly capacity credits, remaining credits, reset time, and window length are available. The pace calculation MUST use credit totals rather than averaging per-account percentages, because weekly ChatGPT quota credits are not the same unit as raw request tokens. The dashboard MUST prefer the backend-provided `weeklyCreditPace` object from `GET /api/dashboard/overview` when present, and MAY fall back to a local calculation only for older responses that do not include that field.

#### Scenario: Weekly credits pace uses account reset deadlines

- **WHEN** multiple accounts have weekly quota data with different `resetAtSecondary` values
- **THEN** the system computes each account's expected remaining weekly credits from that account's own reset time and window length before summing totals

#### Scenario: Weekly credits pace excludes inactive or stale usage rows

- **WHEN** an account is not active or its latest weekly usage sample is older than the freshness window derived from the usage refresh interval
- **THEN** the account is not included in weekly pace totals or forecasts
- **AND** the response reports the excluded stale account count separately from the included account count

#### Scenario: Current schedule gap is separate from forecast shortfall

- **WHEN** actual remaining weekly credits are lower than scheduled remaining weekly credits
- **THEN** the response reports `scheduleGapCredits` for the current deficit against the linear schedule
- **AND** the response reports `projectedShortfallCredits` only for a future shortfall forecast based on recent burn
- **AND** the dashboard labels the two concepts separately

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

- **WHEN** no account has complete, active, fresh weekly credits pace data
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

The dashboard SHALL expose global limit warm-up controls in Settings and per-account opt-in/status in account views. The global default SHALL be disabled.

#### Scenario: Configure warm-up behavior
- **WHEN** an operator opens Settings
- **THEN** the dashboard shows controls for enabling limit warm-up, selecting primary/secondary/both windows, setting the warm-up model, setting the prompt, and setting the cooldown

#### Scenario: Validate warm-up settings before save
- **WHEN** an operator edits warm-up model, prompt, or cooldown fields
- **THEN** the dashboard enforces the same non-empty, max-length, and integer cooldown bounds as the backend API before enabling save

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

The `/reports` page SHALL expose visible filter controls for start date, end date, account, and model.

#### Scenario: Reports page shows report filter controls

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page exposes visible filter controls for start date, end date, account, and model

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
