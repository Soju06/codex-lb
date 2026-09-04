## MODIFIED Requirements

### Requirement: Account management page supports account import and OAuth add flows

The Accounts page SHALL support account import, untargeted OAuth account
addition, and targeted OAuth reauthentication. Reauthentication MUST preserve
separate local seats that share one workspace `chatgpt_account_id`.

The account import flow SHALL allow one or more `auth.json` files to be selected
at once. It MUST import the selected files sequentially in selection order by
sending each file through one existing `POST /api/accounts/import` request, so
the server's single-file multipart contract and resource limits remain
unchanged. The dialog SHALL close and clear its selection only after every file
succeeds.

If an import fails after earlier files succeeded, the flow MUST stop before
attempting later files, keep the dialog open, and retain the failed file and all
unattempted files for retry without retaining already successful files. Every
successful request MUST continue to refresh the account list.

#### Scenario: Account import

- **WHEN** a user selects multiple auth.json files and submits the import flow
- **THEN** the app calls `POST /api/accounts/import` once per file, sequentially in selection order
- **AND** refreshes the account list after each successful request
- **AND** closes and clears the import dialog after every selected file succeeds

#### Scenario: Multi-file account import stops on failure

- **GIVEN** a user submits multiple selected auth.json files
- **AND** at least one earlier file has imported successfully
- **WHEN** a later file fails to import
- **THEN** the app does not attempt any files after the failed file
- **AND** the dialog remains open with the failed and unattempted files retained for retry
- **AND** files that already succeeded are not retained for retry

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
