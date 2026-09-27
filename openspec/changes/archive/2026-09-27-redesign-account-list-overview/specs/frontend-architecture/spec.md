## MODIFIED Requirements

### Requirement: Accounts page

The Accounts page SHALL display a full-width searchable account overview in List and Grid modes, with import and add account actions. Selecting an account SHALL open its existing details, usage, token info and management actions in a responsive dialog. A valid `selected` account URL parameter SHALL open that account on initial load; closing the dialog SHALL remove that parameter without clearing search, filters, sort or pagination. The Accounts page SHALL also let operators view and update whether an account is authorized for upstream cybersecurity work without losing existing account actions such as pause, resume, re-authenticate, export, and delete.

The layout SHALL fit mobile, tablet, and desktop dashboard widths without horizontal page overflow caused by fixed-width account controls.

The Accounts page SHALL keep the add account button above the paginated account rows so it remains reachable without opening account details, and SHALL bound the number of rendered accounts through pagination in both views.

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
- **THEN** the account overview fills the available width and selected account details open in a responsive dialog
- **AND** account list filters, quota rows, proxy controls, routing policy controls, token status, and action buttons fit within the viewport without horizontal document overflow

#### Scenario: Add account remains outside account rows

- **WHEN** the Accounts page renders the account list controls
- **THEN** the add account button is not a child of the account rows
- **AND** the button remains available in the overview controls without opening account details

#### Scenario: Long account list uses pagination

- **WHEN** more than 24 accounts match the List filters
- **THEN** at most 24 account rows are rendered with navigation and a matching-results count
- **AND** add account remains outside the account rows

#### Scenario: Re-authentication-required account is labeled separately

- **WHEN** an account summary has `status = "reauth_required"`
- **THEN** the account list and account detail status badge show `Re-auth required`
- **AND** the account can be found with the status filter for `reauth_required`
- **AND** the account detail exposes the re-authenticate action
- **AND** the account detail does not expose pause or resume actions that could bypass re-authentication
- **AND** the account list and account detail do not expose routing-policy controls that imply the account is selectable while operator recovery is required

### Requirement: Accounts grid overview

The Accounts page MUST offer an accessible list/grid toggle, default to list, and remember the selected view locally independently of Dashboard appearance. Both views MUST share search, status filters, sorting and account selection. Both views MUST fit mobile and desktop widths without horizontal overflow and render at most 24 accounts per page with navigation and a matching-results count. Cards and list rows MUST show identity/workspace, plan, status, quota and reset timing, subscription term, request usage totals, token status, routing/warm-up, and available credits using existing summary data. The List view MUST present labeled account summary fields in dense rows that stack at narrow widths. Selecting a card or list row MUST open the existing account detail/actions and preserve read-only restrictions. Grid and List rendering MUST NOT fetch trends or credit details separately for each account.

#### Scenario: Switch views with active filters

- **WHEN** the operator filters accounts and switches between list and grid
- **THEN** the search/filter/sort values and matching account set are preserved
- **AND** reloading restores the selected view

#### Scenario: Many accounts match

- **WHEN** more than 24 accounts match the current filters
- **THEN** at most 24 cards or list rows are rendered and the operator can navigate to remaining results
- **AND** filtering or sorting restarts pagination from the first page

#### Scenario: Manage an account from a grid card

- **WHEN** an operator opens a grid card
- **THEN** the selected account's existing details and permitted actions are available
- **AND** closing the detail returns to the same filtered grid page

#### Scenario: List view scans complete account summaries

- **WHEN** the operator switches to List view
- **THEN** each row shows identity/workspace, plan/status, subscription term, quota/reset timing, token status, request usage totals, credits, routing/warm-up and a way to open existing details
- **AND** the row uses the already loaded account summary without per-row network requests

#### Scenario: Open and close account details from a list row

- **WHEN** the operator opens an account row using a pointer or keyboard
- **THEN** the account details dialog shows that account and permitted actions
- **AND** closing it returns to the same filtered, sorted and paginated list

### Requirement: AccountListItem displays a reset-credits count badge

The compact `AccountListItem` SHALL render a count badge pinned to the right-upper radius of the item whenever the account reports `available_reset_credits > 0` and dashboard setting `show_reset_credit_badges` is enabled. The badge SHALL display the integer count, capped visually at `"99+"` when the count exceeds 99. The badge SHALL be absent when `available_reset_credits` is `0` or `show_reset_credit_badges` is disabled. The full-width Accounts List overview row SHALL display an explicitly labeled reset-credit count in its usage/credits column under the same visibility conditions.

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

#### Scenario: Overview row labels the reset-credit count
- **WHEN** an Accounts List overview row renders an account with three available reset credits and badges enabled
- **THEN** the usage/credits column displays a labeled count of three reset credits
- **AND** disabling badges hides this count

## REMOVED Requirements

### Requirement: Accounts list uses available tall-viewport space

**Reason:** The full-width paginated overview replaces the sidebar/detail layout and its nested viewport-height scroll region. Both views bound rich rendering to 24 accounts per page.

**Migration:** Keep filters and Add account above the account rows; use page scrolling and pagination for results. Open account management in the shared responsive dialog.
