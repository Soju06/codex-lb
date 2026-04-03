## MODIFIED Requirements

### Requirement: Dashboard exposes sticky-session administration

The system SHALL provide dashboard APIs for listing sticky-session mappings, deleting one mapping, deleting multiple mappings, and purging stale mappings.

#### Scenario: List sticky-session mappings

- **WHEN** the dashboard requests sticky-session entries
- **THEN** the response includes each mapping's `key`, `account_id`, `kind`, `created_at`, `updated_at`, `expires_at`, and `is_stale`
- **AND** the response includes the total number of stale `prompt_cache` mappings that currently exist beyond the returned page

#### Scenario: List only stale mappings

- **WHEN** the dashboard requests sticky-session entries with `staleOnly=true`
- **THEN** the system applies stale prompt-cache filtering before enforcing the result limit

#### Scenario: Filter mappings by account search

- **WHEN** the dashboard requests sticky-session entries with an `accountQuery`
- **THEN** the system returns only mappings whose account display identifier matches that query
- **AND** applies the same filter to the reported total before pagination

#### Scenario: Filter mappings by sticky-session key search

- **WHEN** the dashboard requests sticky-session entries with a `keyQuery`
- **THEN** the system returns only mappings whose sticky-session key matches that query
- **AND** applies the same filter to the reported total before pagination

#### Scenario: Sort mappings by a supported field

- **WHEN** the dashboard requests sticky-session entries with a supported `sortBy` and `sortDir`
- **THEN** the system orders the returned mappings by that sort
- **AND** applies the same ordering consistently across paginated results

#### Scenario: Delete one mapping

- **WHEN** the dashboard deletes a sticky-session mapping by both `key` and `kind`
- **THEN** the system removes that mapping and returns a success response

#### Scenario: Delete multiple mappings

- **WHEN** the dashboard requests deletion of multiple sticky-session mappings identified by `(key, kind)`
- **THEN** the system attempts each deletion independently
- **AND** the response reports which mappings were deleted successfully
- **AND** the response reports which mappings failed to delete

#### Scenario: Bulk delete supports all sticky-session kinds

- **WHEN** the dashboard requests bulk deletion for a mix of `codex_session`, `sticky_thread`, and `prompt_cache` mappings
- **THEN** the system applies the same deletion behavior to each requested mapping regardless of kind

#### Scenario: Delete all mappings that match the active filtered list query

- **WHEN** the dashboard requests filtered bulk deletion with the active list filters
- **THEN** the system deletes all sticky-session mappings that match that filtered query
- **AND** the response reports the number of deleted mappings

#### Scenario: Purge stale prompt-cache mappings

- **WHEN** the dashboard requests a stale purge
- **THEN** the system deletes only stale `prompt_cache` mappings and leaves durable mappings untouched
