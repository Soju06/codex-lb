## ADDED Requirements

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
