## MODIFIED Requirements

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

## ADDED Requirements

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
