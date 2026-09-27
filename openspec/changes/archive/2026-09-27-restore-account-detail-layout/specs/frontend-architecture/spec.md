## MODIFIED Requirements

### Requirement: Accounts page

The Accounts page SHALL offer Detail, List and Grid views with shared search, import and add account actions. Detail view SHALL preserve the original two-column layout on desktop: compact account selector on the left and the selected account statistics, quota trend charts, subscription term, token information and management actions inline on the right. Selecting an account in Detail view SHALL update the right panel without opening a dialog. List and Grid SHALL remain optional full-width account overviews; selecting an overview account SHALL open its details in a responsive dialog. A valid `selected` account URL parameter SHALL select that account on initial load, inline in Detail or in the overview dialog; closing the dialog SHALL remove that parameter without clearing search, filters, sort or pagination. The Accounts page SHALL also let operators view and update whether an account is authorized for upstream cybersecurity work without losing existing account actions such as pause, resume, re-authenticate, export, and delete.

The layout SHALL fit mobile, tablet, and desktop dashboard widths without horizontal page overflow caused by fixed-width account controls.

The Accounts page SHALL keep Add account and filters outside account rows. In Detail view the selector SHALL use a bounded internal scroll region and a content-sized left card. In List and Grid the number of rendered accounts SHALL be bounded through pagination.

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
- **THEN** Detail view stacks the account selector above the inline account detail; List and Grid fill the available width with responsive detail dialogs
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

The Accounts page MUST offer accessible Detail, List and Grid choices, default to Detail when no valid preference is stored, and remember the selected view locally independently of Dashboard appearance. Valid stored List/Grid preferences MUST remain supported. All views MUST share search, status filters, sorting and account selection. All views MUST fit mobile and desktop widths without horizontal overflow. List and Grid MUST render at most 24 accounts per page with navigation and a matching-results count. Cards and list rows MUST show identity/workspace, plan, status, quota and reset timing, subscription term, request usage totals, token status, routing/warm-up, and available credits using existing summary data. The List view MUST present labeled account summary fields in dense rows that stack at narrow widths. Selecting a card or list row MUST open the existing account detail/actions and preserve read-only restrictions. Grid and List rendering MUST NOT fetch trends or credit details separately for each account.

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

#### Scenario: List view scans complete account summaries

- **WHEN** the operator switches to List view
- **THEN** each row shows identity/workspace, plan/status, subscription term, quota/reset timing, token status, request usage totals, credits, routing/warm-up and a way to open existing details
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
