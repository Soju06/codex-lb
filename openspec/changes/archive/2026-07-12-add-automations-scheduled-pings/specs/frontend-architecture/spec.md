## ADDED Requirements

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
