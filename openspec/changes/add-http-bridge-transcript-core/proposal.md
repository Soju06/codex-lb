# Change: add HTTP bridge transcript core

## Why

The HTTP bridge currently retains operation identity and event spools, but no
versioned storage for the complete output items or the account-neutral input
snapshot needed by a future recovery reader.  The first rollout must expand
the schema and establish deterministic helper contracts without changing
request behavior.

## What Changes

- Add one additive Alembic revision with version, output-item, replay-input,
  completeness, and turn-count fields on `http_bridge_operations`.
- Add indexes for session/state/creation lookups and response/state recovery
  lookups.
- Add pure helpers for output-item identity, echo matching, and exact
  tool-call/output de-duplication.
- Keep capture and recovery unwired; later releases will gate those paths
  independently.

## Capabilities

### Modified Capabilities

- `database-migrations`
- `responses-api-compat`

## Impact

This is an expand-only schema and helper release. Existing rows receive
defaults, and no endpoint, setting, or runtime replay behavior changes.
