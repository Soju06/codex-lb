# Tasks

## 1. Implementation

- [x] 1.1 Surface `continuity_owner_conflict` and the selection message on the direct HTTP stream fail-closed paths.
- [x] 1.2 Record reason `owner_conflict` and propagate the selection error code in continuity fail-closed telemetry and request logs.
- [x] 1.3 Keep non-conflict owner failures on `previous_response_owner_unavailable` with the existing reasons and upstream codes.
- [x] 1.4 Add the `idx_accounts_chatgpt_account_id` index (model metadata + concurrent migration with invalid-index repair).

## 2. Regression Coverage

- [x] 2.1 Cover the compact and direct-stream routes for turn-state owner resolution and conflict fail-closed behavior.
- [x] 2.2 Cover that a stale session header does not reach selection when a hard turn-state owner resolves.

## 3. Validation

- [x] 3.1 Run focused proxy compact/responses/utils tests.
- [x] 3.2 Run Ruff and type checks on changed Python files.
- [x] 3.3 Run strict OpenSpec validation.
- [x] 3.4 Verify a single linear alembic head.
