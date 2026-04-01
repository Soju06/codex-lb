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

#### Scenario: Purge stale prompt-cache mappings

- **WHEN** the dashboard requests a stale purge
- **THEN** the system deletes only stale `prompt_cache` mappings and leaves durable mappings untouched
