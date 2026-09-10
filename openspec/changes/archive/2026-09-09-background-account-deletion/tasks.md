## 1. Schema

- [x] 1.1 Add `accounts.delete_requested_at` and
      `accounts.delete_history_requested` columns (model + Alembic revision
      `20260816_000000_add_account_pending_deletion` on the current head,
      guarded upgrade/downgrade).
- [x] 1.2 Partial queue index `idx_accounts_delete_requested_at`
      (`(delete_requested_at, id) WHERE delete_requested_at IS NOT NULL`) so
      the per-interval pending probe and the queue-order scan never touch the
      full accounts table.

## 2. Fast delete path

- [x] 2.1 `AccountsRepository.begin_delete`: terminal `DEACTIVATED` mark +
      pending marker + sticky/bridge cleanup in one short transaction;
      idempotent, first request freezes the `delete_history` choice.
- [x] 2.2 Hide marked accounts from `list_accounts` / `list_accounts_by_ids`;
      block reactivation of marked accounts; keep the DELETE response
      contract (`{"status": "deleted"}`).
- [x] 2.3 Clear the marker in `_apply_account_updates` so credential
      replacement supersedes a pending deletion.
- [x] 2.4 Hide marked accounts from the credential-export endpoints (account
      export, auth export, opencode auth export): 404 during the drain
      window, matching the synchronous delete's contract.
- [x] 2.5 Wipe the stored token ciphertext in `begin_delete` so readers that
      do not know the marker (pre-upgrade replicas during a rolling deploy)
      cannot export usable credentials mid-drain; backfill the non-secret
      seat identity (`chatgpt_user_id`) from the id-token claims first so
      targeted reauthentication can still verify and supersede.
- [x] 2.6 Remove the account's API-key assignments in `begin_delete` (the
      projection the synchronous FK cascade produced): key listings and
      pooled-usage reads exclude the account immediately, scope flag intact.
- [x] 2.7 Fence ordinary status writers (`update_status`,
      `update_status_if_current`) on `delete_requested_at IS NULL` so stale
      in-flight settlements cannot resurrect a marked account.
- [x] 2.8 Treat non-wiped credential ciphertext on a marked row as a
      supersede (replacement by a pre-upgrade replica that cannot clear the
      marker): chunks and finalization clear the marker and abandon.
- [x] 2.9 Reject marked accounts in API-key assignment validation
      (`ApiKeysRepository.list_accounts_by_ids`) so post-DELETE key updates
      cannot recreate assignments for the account.
- [x] 2.10 Atomic marker re-check in `replace_account_assignments`
      (conditional INSERT…SELECT, `FOR SHARE` on PostgreSQL) so validation
      that raced the DELETE cannot recreate an assignment.
- [x] 2.11 Finalization upgrades the account row to `FOR UPDATE` before the
      residual sweeps (PostgreSQL) so in-flight FK inserts are either swept
      or fail post-delete — no live orphans, no surviving history.
- [x] 2.12 Per-chunk self-heal of drift written by unfenced pre-upgrade
      replicas: re-assert terminal status and re-remove recreated API-key
      assignments under the chunk's row lock.
- [x] 2.13 Repeat DELETE short-circuits on an unlocked marker+wipe read so
      the millisecond contract holds while a drain chunk holds the account
      row lock / SQLite writer section.
- [x] 2.14 Treat marked accounts as absent on every ID-based account surface
      (trends, reset-credit read/consume on both route families, probe,
      pause, update, alias, limit-warmup, routing policy, upstream-proxy
      binding, `/v1` reset-credit redemption) via a marker-aware fetch or an
      atomic write predicate; filter the marker in the unscoped API-key pool
      query (`list_all_accounts`) as well.
- [x] 2.15 Propagate the delete-request cache invalidation after a chunk
      repairs pre-upgrade-replica drift, so cached drift stops being served.

## 3. Background worker

- [x] 3.1 `app/modules/accounts/deletion.py`: chunked drain
      (usage_history, additional_usage_history, request_logs; 1k rows per
      transaction, marker re-check under the account row lock per chunk, no
      fold-state lock) for both variants; round-robin at most one nonempty
      chunk per pending account per round with a pending re-scan between
      rounds.
- [x] 3.1a Pin chunk batch selection to the account-leading indexes
      (`account_id` range predicate + index-order ORDER BY →
      `idx_usage_account_time`, `ix_additional_usage_distinct_labels`,
      covering `idx_logs_account_kind_deleted_latest`), verified against the
      production planner; skip re-probing tables observed empty within a
      pass; pause between row-touching rounds proportionally to round
      duration.
- [x] 3.2 Finalization via `AccountsRepository.delete(only_pending=True)`:
      historical transaction shape (identity lock → fold-state lock →
      residual rows → mirrors → sticky/rollup/account) plus marker guard and
      persisted-variant read.
- [x] 3.3 Leader-gated scheduler (30 s tick, cheap pending pre-check before
      leader election, local wake from the delete path), wired into the app
      lifespan; post-finalization cache invalidation mirroring the old
      synchronous path.

## 4. Validation

- [x] 4.1 Integration coverage: chunk-boundary drain (both variants), fold
      pass interleaved between chunks (no folded-row resurrection, orphaned
      dimension preserves history), restart resume, straggler row settled
      mid-drain, repeat-request idempotency without variant escalation,
      supersede by replacement (including the drain/finalize race), fast-path
      API contract (immediate hide, 404 reactivate, 404 credential exports),
      round-robin interleave and mid-pass pickup of newly marked accounts.
- [x] 4.2 Update the existing delete API tests to drive the worker pass;
      keep the direct synchronous `AccountsRepository.delete` coverage.
- [x] 4.3 `ruff check` + `ruff format` + architecture checks + focused
      account/rollup/migration test suites + strict OpenSpec validation.
- [x] 4.4 Alembic round-trip coverage for
      `20260816_000000_add_account_pending_deletion` (parent -> revision ->
      downgrade -> guarded upgrade -> head), wired into the PostgreSQL CI
      target list; downgrade REFUSES while any deletion is queued (the
      marker columns are the queue's only durable state) and the refusal is
      covered by the round-trip test.
