## Why

Auth Guardian currently carries SQLAlchemy `Account` instances beyond the session that loaded them. A normal background-session rollback expires those instances, so any stale eligible account aborts the whole proactive refresh pass with `DetachedInstanceError` now that the guardian is enabled by default.

## What Changes

- Require Auth Guardian to preserve stable candidate identities across the candidate-query session boundary.
- Snapshot candidate account IDs while the query session is active, then perform each refresh in its separately owned session.
- Add a regression test that exercises the real SQLAlchemy session lifecycle instead of relying only on an in-memory repository fake.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Auth Guardian refresh passes must remain executable after the candidate-query session closes.

## Impact

- Affected code: `app/core/auth/guardian.py`.
- Affected tests: Auth Guardian unit coverage with a real async SQLAlchemy session lifecycle.
- No API, schema, migration, configuration, dependency, or dashboard changes.
