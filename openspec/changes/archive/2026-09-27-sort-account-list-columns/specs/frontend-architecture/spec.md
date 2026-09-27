## MODIFIED Requirements

### Requirement: Accounts list supports explicit sort modes

The Accounts page account list SHALL expose sort modes for reset time
soonest-first, reset time latest-first, account name ascending, and account name
descending, plan alphabetically ascending/descending, recorded subscription deadline soonest/latest, and remaining 5h/7d quota percent lowest/highest. The default sort mode SHALL remain most reset credits first. The
same selected sort mode SHALL apply to both the rendered account list and the
page-level selected-account fallback.

Plan sorting SHALL compare plan labels case-insensitively and place empty/unknown plans last. Subscription sorting SHALL compare explicit recorded deadlines without substituting token expiry or reset times. Quota sorts SHALL independently compare their selected window's remaining percentage regardless of the quota appearance preference; absent windows SHALL remain unknown. Unknown values SHALL sort last in both directions, zero quota SHALL remain valid, and equal values SHALL retain deterministic tie breakers. Sorting SHALL precede pagination and changing the sort through either control SHALL reset pagination to the first page while preserving filters and selection. The selected sort SHALL remain shared across Detail, List and Grid.

#### Scenario: Most reset credits remains the default

- **WHEN** the account list renders without an explicit sort mode
- **THEN** accounts with the most available reset credits sort first

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

#### Scenario: Plan and subscription ordering
- **WHEN** an operator selects a Plan or Subscription sort direction
- **THEN** rows order by plan label or recorded deadline in that direction
- **AND** unavailable values remain last without inferring subscription time from credentials

#### Scenario: Independent quota sorting
- **WHEN** an operator sorts by 5h or 7d remaining quota
- **THEN** rows order numerically by that window in the selected direction, including zero percent
- **AND** absent windows remain last without substituting monthly or the other window

#### Scenario: Sort from a later filtered page
- **WHEN** the operator changes sorting on a later page of filtered List results
- **THEN** the full matching collection is sorted before rendering its first page
- **AND** filters and account selection remain intact when changing views


### Requirement: Accounts grid overview

The Accounts page MUST offer accessible Detail, List and Grid choices, default to Detail when no valid preference is stored, and remember the selected view locally independently of Dashboard appearance. Valid stored List/Grid preferences MUST remain supported. All views MUST share search, status filters, sorting and account selection. All views MUST fit mobile and desktop widths without horizontal overflow. List and Grid MUST render at most 24 accounts per page with navigation and a matching-results count. Grid cards MUST show identity/workspace, plan, status, quota and reset timing, subscription term, request usage totals, token status, routing/warm-up, and available credits using existing summary data. List rows MUST show only identity/workspace context, plan/status, a compact recorded-plan duration and primary/weekly or monthly-only quota/reset timing, with an affordance to open details and a compact available-reset-count badge when enabled. List rows MUST omit request/token totals, cost, credentials, purchased credits, routing/warm-up details and long subscription timestamps. The desktop List MUST place short and weekly quota windows side by side and use compact spacing; narrow screens MUST wrap its groups within the viewport. Selecting a card or list row MUST open the existing account detail/actions and preserve read-only restrictions. Grid and List rendering MUST NOT fetch trends or credit details separately for each account.

#### Scenario: Switch views with active filters

- **WHEN** the operator filters accounts and switches between Detail, List and Grid
- **THEN** the search/filter/sort values and matching account set are preserved
- **AND** reloading restores the selected view

#### Scenario: Many accounts match

- **WHEN** more than 24 accounts match the List or Grid filters
- **THEN** at most 24 cards or list rows are rendered and the operator can navigate to remaining results
- **AND** filtering or sorting restarts pagination from the first page

#### Scenario: Manage an account from a grid card

- **WHEN** an operator opens a grid card
- **THEN** the selected account's existing details and permitted actions are available
- **AND** closing the detail returns to the same filtered grid page

#### Scenario: List view scans essential account information

- **WHEN** the operator switches to List view
- **THEN** each row shows identity/workspace, plan/status, compact subscription duration, quota/reset timing and a way to open existing details
- **AND** request/token totals, credentials, purchased credits and long metadata blocks are absent from the row
- **AND** the row uses the already loaded account summary without per-row network requests

#### Scenario: Open and close account details from a list row

- **WHEN** the operator opens an account row using a pointer or keyboard
- **THEN** the account details dialog shows that account and permitted actions
- **AND** closing it returns to the same filtered, sorted and paginated list

#### Scenario: Default view retains original account statistics and charts

- **WHEN** Accounts opens without a valid stored view preference
- **THEN** Detail view shows the compact selector and selected-account statistics and quota trend charts inline
- **AND** no account detail dialog opens merely from selecting a compact account row

#### Scenario: Return to the original layout

- **WHEN** an operator switches from an overview to Detail
- **THEN** the selected account, search, status and sort remain preserved
- **AND** the original inline statistics and charts appear for that selected account
- **AND** only the selected account requests trend/credit details

#### Scenario: Long selector remains bounded in Detail view

- **WHEN** the Detail selector has more rows than its available height
- **THEN** rows scroll inside the left selector
- **AND** filters and Add account remain outside that scroll region

#### Scenario: Full information remains available on selection

- **WHEN** an operator opens a compact List row
- **THEN** selected details retain complete subscription metadata, token information, request totals and management actions

### Requirement: AccountListItem displays a reset-credits count badge

The compact `AccountListItem` SHALL render a count badge pinned to the right-upper radius of the item whenever the account reports `available_reset_credits > 0` and dashboard setting `show_reset_credit_badges` is enabled. The badge SHALL display the integer count, capped visually at `"99+"` when the count exceeds 99. The badge SHALL be absent when `available_reset_credits` is `0` or `show_reset_credit_badges` is disabled. The full-width Accounts List SHALL show a compact labeled count of available resets when positive and show_reset_credit_badges is enabled. It SHALL omit zero/missing counts and disabled badges. The compact selector and Grid header SHALL retain their existing badges. The List badge SHALL use summary data without extra credit requests or a direct redemption action.

#### Scenario: Badge shows the available count
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 3`
- **THEN** a count badge pinned to the item's right-upper radius displays `3`

#### Scenario: Badge caps at 99+
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 120`
- **THEN** the count badge displays `99+`

#### Scenario: Badge absent when zero
- **WHEN** an `AccountListItem` renders for an account with `available_reset_credits: 0`
- **THEN** no count badge is rendered

#### Scenario: Badge visibility follows settings
- **GIVEN** `show_reset_credit_badges` is disabled
- **AND** an account reports `available_reset_credits: 3`
- **WHEN** an `AccountListItem` renders
- **THEN** the reset-credit count badge is absent

#### Scenario: List shows available reset count
- **WHEN** an Accounts List row has three available reset credits and badges are enabled
- **THEN** a compact Reset (3) indicator appears beside plan/status
- **AND** disabling badges or having zero/missing credits hides the indicator
- **AND** selecting the row opens details without redeeming credits

## ADDED Requirements

### Requirement: Accounts List headers control sorting

Desktop List SHALL offer keyboard-operable Plan, Subscription, Quota 5h and Quota 7d header controls. Selecting an inactive header SHALL sort ascending; selecting it again SHALL toggle direction. Active controls SHALL expose visible direction arrows and an accessible direction description. The same ascending/descending modes SHALL be available from the existing sort dropdown at all viewport sizes. Header sorting SHALL preserve the compact row layout.

#### Scenario: Toggle header direction
- **WHEN** an operator activates a List sort header by pointer or keyboard
- **THEN** its ascending order appears with a visible and accessible direction
- **AND** activating it again reverses that order and updates the sort dropdown

#### Scenario: Mobile sorting
- **WHEN** desktop headers are hidden at narrow widths
- **THEN** the operator can select every new sort mode from the sort dropdown
