# Verification

The file route now preserves typed transport provenance through `CodexClient`, the file client, and `ProxyService`. The existing unary retry loop performs account exclusion, deadline checks, and strict-owner enforcement. No additional retry loop or reservation acquisition was added.

## Coverage

- The actual file routes exercise 15 combinations: create, unpinned finalize, and pinned finalize against proxy connection refusal, TLS verification failure, response-body failure, ambiguous request failure, and process-wide network failure.
- The successful create failover verifies the durable file owner belongs to the account that completed the upload.
- Proxy endpoint IDs deliberately contain `timeout` so negative controls detect accidental message-based replay.
- Unit controls verify typed replay permission, typed refusal despite transient-looking text, body-read denial, process-network denial, and unchanged legacy classification.

## Local results

- Before the product change, the routed connection-refusal regression returned 502 instead of completing through the eligible fallback account.
- `.venv/bin/python -m pytest tests/integration/test_proxy_files.py tests/unit/test_files_client.py tests/unit/test_unary_transport_failover.py -q`: 63 passed.
- `.venv/bin/python -m pytest tests/unit/test_unary_transport_failover.py tests/unit/test_proxy_utils.py -k 'unary_failover_observes or files_create or files_finalize or thread_goal or previsible_unary or transcribe' -q`: 40 passed, 1374 deselected.
- Scoped Ruff checks and formatting, full `.venv/bin/ty check`, proxy architecture, cancellation safety, and proxy timing seam checks passed.
- `openspec validate fix-files-routed-connect-failover --strict` passed. The affected main spec has 95 pre-existing strict diagnostics; baseline and updated diagnostics were compared and are identical.

- `uv run pre-commit run local-ci --hook-stage manual --all-files` ran with isolated SQLite and PostgreSQL targets. Frontend lint, typecheck, coverage tests, build, and proxy/settings ratchets passed. The gate then stopped at the unchanged upstream Alembic graph: 2 heads and the duplicate `20260914_000000` timestamp. Later full-gate stages did not run.

These tests inject failures at the routed transport boundary and use isolated test databases. They do not establish production deployment or real upstream-network behavior.
