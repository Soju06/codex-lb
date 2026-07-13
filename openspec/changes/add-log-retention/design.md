## Context

Only per-account cleanup deletes rows from `request_logs`; `usage_history`/`additional_usage_history` are never pruned. The `add-account-usage-rollup` change (this branch's base) makes request-log pruning safe for lifetime totals: folded rows' contributions persist in `account_usage_rollups`, and the fold watermark (`account_usage_rollup_state.folded_through`) marks exactly which rows are folded.

Consumers that constrain retention floors:

- Reports/overview query arbitrary user-selected ranges; usage service windows reach 7 days; monthly usage windows reach ~31 days.
- `latest_by_account` reads each account's newest usage row regardless of age — a paused account's only row may be months old.
- `find_latest_account_id_for_response_id` resolves `previous_response_id` owners from request logs (practically recent, bounded by client session lifetimes).

## Goals / Non-Goals

**Goals:** bounded table growth for opted-in operators; zero data-semantics change while disabled (default); pruning can never corrupt lifetime account totals or drop an account's last-known usage.

**Non-Goals:** partitioning (heavier migration; batched deletes suffice at these volumes), archival/export of pruned rows, dashboard-configurable retention (env-only, matching other ops schedulers), conversation-archive retention (separate storage).

## Decisions

### D1. Env settings, opt-in, validated floors

`request_log_retention_days` (0 = off, else ≥ 30) and `usage_history_retention_days` (0 = off, else ≥ 45; one knob for both usage tables — they serve the same feature and diverge only in key shape). Floors keep every in-product consumer (7-day windows, monthly windows ~31 days, default report ranges) inside retained data; a pydantic validator rejects unsafe values at startup (fail fast). Env-only follows the precedent of the other background schedulers.

### D2. Request-log cutoff is `min(now − retention, rollup watermark)`

Pruning must never delete an unfolded row: its tokens would silently vanish from lifetime summaries (the live tail could no longer see it, and the rollup never received it). The rollup's 24 h lag plus a ≥ 30-day retention floor means the watermark virtually always leads the cutoff; the `min()` is the correctness guard for cold starts (fold not yet run → no state row → skip request-log pruning entirely) and stalled folds.

### D3. Preserve the latest usage row per identity key

`usage_history` deletion excludes each `(account_id, coalesce(window,'primary'))` group's max-id row; `additional_usage_history` likewise per `(account_id, quota_key, window)`. This keeps `latest_by_account` answers stable for idle/paused accounts whose newest row predates the cutoff. Implemented as `DELETE ... WHERE id IN (SELECT id ... WHERE recorded_at < cutoff AND id NOT IN (latest ids) LIMIT batch)` loops.

### D4. Batched deletes on an hourly, leader-gated scheduler

Deletes run in batches (10,000 ids per transaction, id-subquery form portable across SQLite/PostgreSQL) until a batch comes back short, yielding between batches. Hourly cadence keeps per-pass volumes small in steady state; the first opt-in pass on a large table just runs more batches. Leader election gates multi-instance deployments (same pattern as the fold/cleanup schedulers). After pruning usage history on SQLite, the bulk-history incremental cache is cleared (same invalidation account deletion uses).

### D5. Per-API-key sums fold alongside account sums

Adversarial review caught a consumer the original inventory missed: `ApiKeysRepository.list_usage_summary_by_key` / `get_usage_summary_by_key_id` aggregate `request_logs` lifetime totals per key with no time bound, and the account rollup cannot protect them. A `api_key_usage_rollups` table is folded in the same slice transaction under the same watermark, using the API-key semantics (no dedupe, soft-deleted rows included, warmup excluded); reads merge rollup + tail. The fold's empty-window skip now requires BOTH aggregates empty, because a window can contain only soft-deleted rows that still count toward key totals. Side effect (disclosed): folded key sums persist across account hard-deletes, where the legacy live aggregate would have shrunk.

### D6. Migration resets prior fold state (Codex P1)

Installs that ran the account-rollup change first have an advanced shared watermark; creating `api_key_usage_rollups` empty under that watermark would collapse every key's totals to the live tail. The migration therefore resets the fold state — account rollup rows AND the watermark together, exactly the documented escape hatch — so the next fold pass re-backfills both rollups from raw `request_logs`, with reads falling back to the full live aggregate meanwhile. Pinned by a migration regression test that seeds an advanced watermark before upgrading.

### D7. Snapshot-safe deletes and reader-ordered latest protection (Codex P2s)

- Request-log pruning runs only while the fold is current (watermark ≥ now − 2·lag) and deletes only rows a full fold lag below the watermark. Summary reads load sums+watermark and the live tail in separate statements; a fold committing between them advances the watermark, and deleting from that just-folded window would starve the reader's tail. A reader's loaded watermark trails the current one by at most one steady-state fold advance (≤ the pass interval), so a full-lag margin under a current watermark is unreachable by any live reader; during backfill/stall, pruning suspends entirely.
- Protected usage-history rows are chosen by the readers' ordering (max `recorded_at` per identity, all ties retained) instead of `max(id)`, so backfilled out-of-chronology rows cannot displace the last-known sample.

### D7b. Review-driven hardening

- Protected latest-row id sets are materialized once per pass and passed as literals into the batch deletes; embedding the GROUP BY subquery in every batch statement rescanned the whole table per 10k batch (under the SQLite writer lock). New identities appearing mid-pass are safe: their rows are newer than the cutoff.
- Retention settings gained a ceiling (`le=3650`); absurd values previously validated at startup then overflowed `timedelta` every pass, silently disabling retention.
- The `min(cutoff, watermark)` guard and the coalesce/multi-identity latest-row semantics are pinned by mutation-hardened regression tests (lagging-watermark case, NULL-window identity merge, multi-batch drains).

### D8. New `data-retention` capability spec

Retention is an operator contract (what data is kept, what the floors are, what can never be deleted), not a query-shape concern — a new capability keeps `query-caching` focused.

## Risks / Trade-offs

- [Operator sets retention shorter than their reporting habits] → validated floors + context docs; reports beyond the window simply show retained data only.
- [Huge first prune after opt-in] → bounded batches with per-batch commits; leader-only; hourly resumption finishes the backlog incrementally.
- [`previous_response_id` lookups for conversations older than retention fail] → ≥ 30-day floor dwarfs real client session lifetimes; documented.
- [Pruning races the fold job] → the watermark is read in the same transaction as each delete batch; rows newer than the watermark are untouchable, and folding never mutates rows, only reads them.

## Migration Plan

Code-only. Enable by setting the env vars; disable by unsetting (no data returns, but nothing else changes). Rollback = revert.

## Open Questions

None.
