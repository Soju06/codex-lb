## Why

File ownership pins currently live only in a replica-local dictionary, so a file finalize or Responses request handled by another replica can select an account that does not own the upstream file. The confirmed P1 bug must be fixed by making the existing ownership boundary durable and shared.

## What Changes

- Persist live `file_id -> account_id` ownership pins in the application database with the existing 30-minute lifetime.
- Resolve file ownership through the durable store so finalize and input-file routing remain account-bound across replicas.
- Keep the existing `_pin_file_account` and `_resolve_file_account` service boundaries and fail closed when durable ownership cannot be established safely.
- Add migration and targeted repository/service regression coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Replace process-local best-effort file ownership with durable, replica-shared ownership for file finalize and Responses input-file routing.
- `files-upload-protocol`: Require file finalization to resolve its durable owner through the shared database and fail closed when that lookup is unavailable.
- `sticky-session-operations`: Require a remote HTTP-bridge owner to corroborate signed file-owner metadata with its own fresh durable lookup instead of trusting origin process memory.
- `sticky-session-operations`: Require every receiving HTTP-bridge transport, including terminal compaction, to revalidate forwarded file ownership against the durable database.

## Impact

The change affects the proxy file operations mixin, a small proxy persistence repository, the database model and Alembic graph, and focused proxy/database tests. It adds no dependency or public API surface.
