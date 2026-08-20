## Why

Live usage snapshots can arrive with either the internal codex-lb account ID
or the upstream ChatGPT account identity. Some live paths include a
workspace-local suffix on the internal ID, which does not necessarily identify
the persisted codex-lb account row. Guessing by string shape can attribute one
workspace slot's quota state to another slot and break routing decisions.

## What Changes

- Resolve supplied internal account IDs only when the exact persisted account
  row exists.
- Resolve suffixed or otherwise unknown internal IDs only through a unique
  upstream ChatGPT account identity when that identity is also supplied.
- Drop unresolved or ambiguous live snapshots rather than guessing.
- Coalesce later snapshots under the resolved account ID after a raw ID has
  been resolved.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `live-usage-ingestion`: live snapshot attribution uses explicit identity
  evidence and preserves per-account write coalescing after normalization.

## Impact

- Code: live usage ingestor identity resolution and coalescing.
- Tests: integration coverage for hub-published snapshots, unproven suffix
  drops, and resolved-alias coalescing.
- Configuration, schema, response shapes, and background polling are unchanged.
