## Why

Local August 14, 2026 builds could stamp SQLite databases at the retired
`20260814_020000_merge_identity_and_warmup_heads` merge id even when the
current mainline file-pin, sticky-abandonment-scope, pending-deletion,
API-key reasoning-policy, and model-source-embeddings lineage had never run.
Those databases also kept two artifacts current main no longer owns:
`idx_accounts_chatgpt_account_id` and
`quota_planner_decisions.lease_expires_at`. Current main therefore treats the
stamp as schema-ahead and a drift check on the live clone reports eleven schema
diffs.

## What Changes

- Auto-remap the retired merge stamp to the current pre-repair Alembic head so
  upgrade can continue through normal `python -m app.db.migrate upgrade`.
- Add one forward-only repair migration that replays the guarded current-main
  migrations needed by the stamped-local shape and drops the two stale
  artifacts when present.
- Cover the representative SQLite shape with a regression test that proves the
  repair converges to the current ORM schema.

## Capabilities

### Modified Capabilities

- `database-migrations`: retired local merge stamps upgrade cleanly to the
  current schema without manual restamping.
