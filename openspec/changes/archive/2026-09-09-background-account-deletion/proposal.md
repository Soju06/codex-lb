## Why

`DELETE /api/accounts/{id}` detaches (or deletes) the account's entire raw
history in one transaction while holding the fold-state lock. Measured on
production: ~11.6 s to delete ~93k `usage_history` rows and **313 s** to
soft-detach ~133k `request_logs` rows (18 indexes, every row non-HOT). For
those minutes the fold is blocked, one pool connection is pinned (contributing
to `QueuePool` timeouts), the HTTP client times out, and the long transaction
delays vacuum.

## What Changes

- `DELETE /api/accounts/{id}` becomes a fast mark: the account turns terminal
  (`DEACTIVATED` + a pending-deletion marker), disappears from listings and
  serving immediately, and the API returns within milliseconds with the
  existing `{"status": "deleted"}` contract.
- A new leader-gated background worker drains the account's bulk rows
  (`usage_history`, `additional_usage_history`, `request_logs`) in bounded
  chunks (5k rows per transaction, no fold-state lock), then finalizes in one
  fold-state-locked transaction with the exact shape of the old synchronous
  delete: residual rows, folded-bucket lifecycle mirrors, sticky/rollup rows,
  account row.
- Deletion is restart-safe (all progress in the database), idempotent
  (repeat DELETE requests succeed without escalating the frozen
  `delete_history` choice), supports both `delete_history` variants, and is
  superseded by a credential replacement (re-import/reauth) that clears the
  marker.
- Schema: two new nullable-safe columns on `accounts`
  (`delete_requested_at`, `delete_history_requested`), Alembic revision
  `20260816_000000_add_account_pending_deletion` on the current single head.

## Capabilities

### New Capabilities

- `account-deletion`: asynchronous account deletion lifecycle — fast terminal
  mark, immediate listing/serving exclusion, chunked background drain with
  fold-interleave safety, restart-safe finalization, idempotency, and
  supersede-by-replacement semantics.

### Modified Capabilities

(none — the query-caching lifecycle requirement "rollup row deleted in the
same transaction as the account deletion" continues to hold: the finalization
transaction removes both together.)

## Impact

- `app/modules/accounts/repository.py` (`begin_delete`, marker-guarded
  `delete(only_pending=True)`, listing filters, replacement clears marker),
  `app/modules/accounts/deletion.py` (new worker + scheduler),
  `app/modules/accounts/service.py`, `app/main.py` (scheduler wiring),
  `app/db/models.py`, one Alembic revision.
- No API schema change (`AccountDeleteResponse` unchanged), no frontend
  change (the listing refetch after delete already sees the account gone),
  no new settings.
