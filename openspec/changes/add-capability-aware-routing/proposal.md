## Why

Authenticated Codex orchestration can know that a Responses WebSocket turn
requires Trusted Access for Cyber before codex-lb selects an account. Today
that requirement can be discovered only after an ordinary upstream attempt,
and it is not durable across a reconnect that omits the proxy-generated turn
state.

## What Changes

- Accept one exact authenticated `trusted_cyber` routing signal on the direct
  Responses WebSocket handshake or current `response.create` metadata.
- Narrow the already-eligible account pool to existing
  `security_work_authorized` accounts before the first upstream attempt and on
  every retry; fail closed when none remain.
- Persist the requirement before dispatch as API-key-scoped, domain-separated
  hashes so a same-lineage reconnect remains required without repeating the
  capability signal or generated turn state.
- Strip the internal capability carrier before upstream dispatch, archives,
  request diagnostics, and logging.
- Add one additive, zero-backfill lineage table instead of importing the
  broader migration and lifecycle changes from earlier draft work.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `account-routing`: Require `trusted_cyber` turns to use only already-eligible
  accounts whose existing `security_work_authorized` grant is true.
- `responses-api-compat`: Establish and restore the requirement before direct
  WebSocket account selection while preserving ordinary Responses behavior.
- `sticky-session-operations`: Preserve a monotonic capability requirement
  across API-key-scoped session, turn-state, response, and task lineage.
- `database-migrations`: Add the minimal durable hashed-lineage table on the
  current single Alembic head without a historical-row backfill.

## Impact

- Direct Responses WebSocket ingress, account selection, reconnect request
  state, and internal-header/privacy filtering.
- Proxy lineage persistence and one additive database migration.
- Focused parser, repository, migration, selection, privacy, and realistic
  no-echo reconnect regression coverage.
- No new setting, dependency, account field, dashboard surface, README
  section, reactive retry algorithm, or required setup step.
