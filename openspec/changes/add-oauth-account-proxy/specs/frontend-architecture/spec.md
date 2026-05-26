## MODIFIED Requirements

### Requirement: Accounts page

The Accounts page SHALL display a two-column layout: left panel with searchable account list, import button, and add account button; right panel with selected account details including usage, token info, and actions (pause/resume/delete/re-authenticate).

#### Scenario: Account selection

- **WHEN** a user clicks an account in the list
- **THEN** the right panel shows the selected account's details

#### Scenario: Account import

- **WHEN** a user clicks the import button and uploads an auth.json file
- **THEN** the app calls `POST /api/accounts/import` and refreshes the account list on success

#### Scenario: Account import with proxy

- **WHEN** a user uploads an auth.json file and enters proxy settings in the
  import dialog
- **THEN** the app sends the proxy fields in the same
  `POST /api/accounts/import` multipart request
- **AND** the app MUST NOT call `POST /api/accounts/{accountId}/proxy` after
  import for this flow

#### Scenario: OAuth add account

- **WHEN** a user clicks the add account button
- **THEN** an OAuth dialog opens with browser and device code flow options
- **AND** the dialog renders a collapsible "Configure egress proxy
  (optional)" section below the Browser/Device options

#### Scenario: OAuth add account with proxy

- **WHEN** a user expands the "Configure egress proxy (optional)" section
  in the OAuth dialog and then clicks Browser or Device
- **THEN** the app calls `POST /api/oauth/start` with `expectProxy=true`
  and the validated proxy fields
- **AND** the proxy form remains visible on the browser/device stages while
  the user completes the upstream sign-in
- **AND** when status polling returns `tokens_ready`, the dialog renders a
  "Finish setup" button instead of an automatic success transition
- **AND** clicking Finish calls `POST /api/oauth/complete` with the proxy
  fields, and a 422 `proxy_probe_failed` response keeps the dialog at
  `tokens_ready` with the typed-reason error surfaced so the user can
  correct the proxy and retry without redoing the upstream sign-in
- **AND** the app MUST NOT call `POST /api/accounts/{accountId}/proxy`
  after the OAuth flow completes for this attempt
- **AND** the UI MUST treat browser/device sign-in navigation as the
  operator's own browser/device traffic, not as account egress through
  codex-lb

#### Scenario: Device OAuth start begins polling

- **WHEN** the app starts Device Code OAuth with `POST /api/oauth/start`
- **AND** `expectProxy` is `false` (or unset)
- **AND** the response includes a `deviceAuthId` and `userCode`
- **THEN** the backend starts polling for the device token without requiring a separate `/api/oauth/complete` call
- **AND** a later `/api/oauth/complete` call remains safe and does not start a duplicate polling task

#### Scenario: Device OAuth start with expected proxy defers persistence

- **WHEN** the app starts Device Code OAuth with `POST /api/oauth/start`
- **AND** `expectProxy` is `true`
- **THEN** the backend still starts polling without requiring a separate
  `/api/oauth/complete` call to begin polling
- **AND** when polling acquires tokens, the backend transitions OAuth
  state to `tokens_ready` and does NOT persist the account
- **AND** the app MUST call `POST /api/oauth/complete` with the proxy
  fields to atomically probe and persist

#### Scenario: OAuth copy buttons work on remote HTTP dashboards

- **WHEN** the OAuth dialog displays a browser authorization URL, device
  verification URL, or device user code
- **AND** the browser does not expose the async Clipboard API for the current
  dashboard origin
- **THEN** clicking Copy MUST still attempt a legacy document copy fallback
- **AND** the copied state MUST only be shown after a successful copy attempt

#### Scenario: OAuth dialog reset drops pending state

- **WHEN** the user closes the OAuth dialog (or the dialog is dismissed)
- **THEN** the app calls `POST /api/oauth/reset` to drop any held tokens
  and reset server-side OAuth state
- **AND** the dialog prevents user-initiated close while proxy finalization
  is in progress

#### Scenario: Account actions

- **WHEN** a user clicks pause/resume/delete on an account
- **THEN** the corresponding API is called and the account list is refreshed
