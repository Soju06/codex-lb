## MODIFIED Requirements

### Requirement: Accounts grid overview

The Accounts page MUST offer accessible Detail, List and Grid choices, default to Detail when no valid preference is stored, and remember the selected view locally independently of Dashboard appearance. Valid stored List/Grid preferences MUST remain supported. All views MUST share search, status filters, sorting and account selection. All views MUST fit mobile and desktop widths without horizontal overflow. List and Grid MUST render at most 24 accounts per page with navigation and a matching-results count. Grid cards MUST show identity/workspace, plan, status, quota and reset timing, subscription term, request usage totals, token status, routing/warm-up, and available credits using existing summary data. List rows MUST show only identity/workspace context, plan/status, a compact recorded-plan duration and primary/weekly or monthly-only quota/reset timing, with an affordance to open details. List rows MUST omit request/token totals, cost, credentials, purchased/reset credits, routing/warm-up details and long subscription timestamps. The desktop List MUST place short and weekly quota windows side by side and use compact spacing; narrow screens MUST wrap its groups within the viewport. Selecting a card or list row MUST open the existing account detail/actions and preserve read-only restrictions. Grid and List rendering MUST NOT fetch trends or credit details separately for each account.

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
- **AND** request/token totals, credentials, credits and long metadata blocks are absent from the row
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

The compact `AccountListItem` SHALL render a count badge pinned to the right-upper radius of the item whenever the account reports `available_reset_credits > 0` and dashboard setting `show_reset_credit_badges` is enabled. The badge SHALL display the integer count, capped visually at `"99+"` when the count exceeds 99. The badge SHALL be absent when `available_reset_credits` is `0` or `show_reset_credit_badges` is disabled. The minimal full-width Accounts List SHALL omit reset-credit counts; the compact selector and Grid header SHALL retain their existing badges.

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

#### Scenario: Minimal List omits credit metadata
- **WHEN** an Accounts List row renders an account with available reset credits
- **THEN** the row omits the reset-credit count to preserve its minimal layout
- **AND** credit information remains available through account details
