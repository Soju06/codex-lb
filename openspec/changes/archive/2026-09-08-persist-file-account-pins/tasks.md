## 1. Durable ownership storage

- [x] 1.1 Add the file-account pin ORM model and forward Alembic migration on the current head.
- [x] 1.2 Implement a focused repository for durable upsert and unexpired owner lookup.

## 2. Proxy integration

- [x] 2.1 Wire the repository through the existing file pin/resolve boundaries.
- [x] 2.2 Make multi-file ownership resolution use the durable lookup boundary and preserve fail-closed conflict behavior.

## 3. Verification

- [x] 3.1 Add targeted repository and cross-replica service regression tests, including expiry and multi-file behavior.
- [x] 3.2 Run focused tests, migration checks, OpenSpec validation, and inspect the final diff/status.

## 4. DB-authoritative ownership repair

- [x] 4.1 Remove process-local caching from hard file-owner decisions and batch multi-file resolution through the repository.
- [x] 4.2 Use database-authoritative time for claim expiry, reclaim, live lookup, and opportunistic cleanup.
- [x] 4.3 Add the file-finalize ownership contract to `files-upload-protocol` and replace the stale process-local forwarding contract in `sticky-session-operations`.
- [x] 4.4 Add hermetic race and fail-closed regression coverage, then rerun focused validation.
- [x] 4.5 Make compact settlement and ambiguous owner-forward dispatch single-owner, and add stream/collect/non-200/lost-status regression coverage.
