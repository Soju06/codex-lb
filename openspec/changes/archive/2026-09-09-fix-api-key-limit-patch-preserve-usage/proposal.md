## Why

Updating an existing API-key limit can race with a reservation or lazy reset that commits newer `current_value` and `reset_at` state. The PATCH must change the submitted maximum without clearing that newer usage state, and SQLite snapshot conflicts must not turn the valid PATCH into a 500 response.

## What Changes

- Preserve matched limit usage and reset timestamps when a PATCH changes only the limit maximum.
- Retry the complete API-key update transaction after a transient SQLite lock or snapshot conflict so the retry rereads current limit state.
- Add route and service regression coverage for committed usage preservation and transient SQLite conflicts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Define usage-preserving matched-limit PATCH behavior and transient SQLite retry semantics.

## Impact

The change is limited to API-key service/repository update behavior, focused API-key tests, and the API-key specification. It adds no API fields, settings, dependencies, or migrations.
