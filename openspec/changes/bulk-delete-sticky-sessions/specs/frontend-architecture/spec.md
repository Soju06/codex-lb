## MODIFIED Requirements

### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads, reset priority, prompt-cache affinity TTL), password management (setup/change/remove), TOTP management (setup/disable), API key auth toggle, API key management (table, create, edit, delete, regenerate), and sticky-session administration.

#### Scenario: View sticky-session mappings

- **WHEN** a user opens the sticky-session section on the Settings page
- **THEN** the app fetches sticky-session entries and displays each mapping's kind, account, timestamps, and stale/expiry state

#### Scenario: Filter sticky-session mappings before bulk selection

- **WHEN** a user provides an account search term or sticky-session key search term in the sticky-session section
- **THEN** the app refetches the sticky-session list using those filters
- **AND** resets pagination to the first page for the filtered result set
- **AND** shows only rows that match the active filters

#### Scenario: Reorder sticky-session mappings for cleanup

- **WHEN** a user changes the sticky-session sort by clicking a supported sortable column header
- **THEN** the app refetches the sticky-session list using the selected sort
- **AND** preserves the active filters while resetting pagination to the first page

#### Scenario: Select sticky-session rows on the current page

- **WHEN** a user toggles row checkboxes in the sticky-session table
- **THEN** the UI tracks the selected rows on the current page
- **AND** a header control allows selecting or clearing all currently visible rows on that page

#### Scenario: Bulk delete selected sticky-session rows

- **WHEN** one or more sticky-session rows are selected
- **THEN** the UI enables a `Delete Sessions` action
- **AND** activating that action opens a confirmation dialog that shows the number of selected rows

#### Scenario: Bulk delete all filtered sticky-session rows

- **WHEN** the sticky-session list has active filters and at least one matching row
- **THEN** the UI exposes a `Delete Filtered` action for the current filtered result set
- **AND** activating that action opens a confirmation dialog that references the filtered row count

#### Scenario: Bulk delete refresh preserves table context

- **WHEN** a user confirms bulk deletion of selected sticky-session rows
- **THEN** the app calls the bulk sticky-session delete API
- **AND** refreshes the sticky-session table afterward
- **AND** preserves the current filters and pagination context
- **AND** clears selection for rows that were deleted while retaining selection only for surviving rows

#### Scenario: Bulk delete falls back to the nearest valid page

- **WHEN** bulk deletion removes all rows from the currently visible page
- **THEN** the sticky-session table refreshes to the nearest remaining valid page for the current filters
- **AND** the current filters remain unchanged

#### Scenario: Bulk delete reports partial failures

- **WHEN** bulk sticky-session deletion succeeds for some rows and fails for others
- **THEN** the UI reports both the successful deletions and the failed deletions

#### Scenario: Purge stale prompt-cache mappings

- **WHEN** a user requests a stale purge from the sticky-session section
- **THEN** the app calls the sticky-session purge API and refreshes the list afterward
