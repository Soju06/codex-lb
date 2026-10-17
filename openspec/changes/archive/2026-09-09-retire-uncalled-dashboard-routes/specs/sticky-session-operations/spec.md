## MODIFIED Requirements

### Requirement: Dashboard exposes sticky-session administration
The system SHALL provide dashboard APIs for listing sticky-session mappings, deleting mappings by explicit `key` and `kind` through the batch delete endpoint, and purging stale mappings. The system SHALL NOT expose a per-item `DELETE /api/sticky-sessions/{kind}/{key}` route; deleting one mapping is a one-entry batch delete.

#### Scenario: List sticky-session mappings
- **WHEN** the dashboard requests sticky-session entries
- **THEN** the response includes each mapping's `key`, `account_id`, `kind`, `created_at`, `updated_at`, `expires_at`, and `is_stale`
- **AND** the response includes the total number of stale `prompt_cache` mappings that currently exist beyond the returned page

#### Scenario: List only stale mappings
- **WHEN** the dashboard requests sticky-session entries with `staleOnly=true`
- **THEN** the system applies stale prompt-cache filtering before enforcing the result limit

#### Scenario: Delete one mapping
- **WHEN** the dashboard posts a batch delete containing exactly one `{key, kind}` entry
- **THEN** the system removes that mapping and reports it in `deleted` with `deletedCount` 1
- **AND** a missing or reserved mapping is reported in `failed` with reason `not_found` instead of a route-level error

#### Scenario: Purge stale prompt-cache mappings
- **WHEN** the dashboard requests a stale purge
- **THEN** the system deletes only stale `prompt_cache` mappings and leaves durable mappings untouched
