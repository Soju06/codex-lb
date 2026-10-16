# Upstream prerequisite verification — 2026-10-03

Base: main `f8ffbac2099a113fba54dfd8d77774f5bca80ffa` plus alias PR #2568. This branch isolates source ownership and equivalent-source selection; it contains no native WebSocket runtime or new source capability.

## Local checks

- Source selection, ownership storage/migrations, replay scope and route compatibility: **805 passed, 2 skipped, 2 warnings in 442.23s (0:07:22)**. Command: `TMPDIR=/dev/shm uv run pytest -n 2 --dist=loadfile tests/unit/test_source*.py tests/integration/test_model_source_pool*.py tests/integration/test_source_ownership*.py tests/integration/test_source_pool*.py tests/integration/test_source_reference*.py tests/integration/test_source_prompt_compatibility.py tests/integration/test_source_client_metadata.py -q`.
- Data-retention regressions: **17 passed**.

- `make lint` and whole-repository `uv run ty check app tests` passed.
- SQLite `make migration-check` upgraded to the single ownership-history head and passed schema drift checking.
- Dedicated migration tests cover historical log rows, ownership-history backfill and downgrade/upgrade; two PostgreSQL concurrency regressions skip on SQLite.
- Strict change validation and all 67 main specifications passed.
- No additional dashboard UI; alias screenshots are in the prerequisite.

## Scope adaptation

The port retains current main's subscription replay predicates unchanged and extracts only source-only helpers required for reference classification. It excludes subscription-overflow, model-source pin tables, new-account warmup schema and the fork key dashboard. Namespace forwarding tests explicitly declare `experimental_supported_tools`, preserving upstream's existing capability policy.

## Remaining draft gates

Maintainer product/design acceptance (especially durable history lifetime), PostgreSQL migration/storage execution and full cloud CI/current-head review remain mandatory before merge. Docker API access is unavailable locally. No production runtime/configuration/data was touched. This change remains active and unarchived for contributor review.
