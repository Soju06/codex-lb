# Verification

The file route now preserves typed transport provenance through `CodexClient`, the file client, and `ProxyService`. The existing unary retry loop performs account exclusion, deadline checks, and strict-owner enforcement. No additional retry loop or reservation acquisition was added.

## Coverage

- The actual file routes exercise 15 combinations: create, unpinned finalize, and pinned finalize against proxy connection refusal, TLS verification failure, response-body failure, ambiguous request failure, and process-wide network failure.
- The successful create failover verifies the durable file owner belongs to the account that completed the upload.
- Proxy endpoint IDs deliberately contain `timeout` so negative controls detect accidental message-based replay.
- Unit controls verify typed replay permission, typed refusal despite transient-looking text, body-read denial, process-network denial, and unchanged legacy classification.

## Initial verification

- Before the product change, the routed connection-refusal regression returned 502 instead of completing through the eligible fallback account.
- `.venv/bin/python -m pytest tests/integration/test_proxy_files.py tests/unit/test_files_client.py tests/unit/test_unary_transport_failover.py -q`: 63 passed.
- `.venv/bin/python -m pytest tests/unit/test_unary_transport_failover.py tests/unit/test_proxy_utils.py -k 'unary_failover_observes or files_create or files_finalize or thread_goal or previsible_unary or transcribe' -q`: 40 passed, 1374 deselected.
- Scoped Ruff checks and formatting, full `.venv/bin/ty check`, proxy architecture, cancellation safety, and proxy timing seam checks passed.
- `openspec validate fix-files-routed-connect-failover --strict` passed. The validator used for the initial verification reported 95 pre-existing strict diagnostics in the affected main spec; baseline and updated diagnostics were compared and were identical.

- `uv run pre-commit run local-ci --hook-stage manual --all-files` ran with isolated SQLite and PostgreSQL targets. Frontend lint, typecheck, coverage tests, build, and proxy/settings ratchets passed. The gate then stopped at the unchanged upstream Alembic graph: 2 heads and the duplicate `20260914_000000` timestamp. Later full-gate stages did not run.

## Verification after upstream migration repair

- Integrated upstream `main` at `09a140fa9979a908e60acc97232367e0a08ef32c`, including the migration repair from #2461, without further product-code or test changes.
- Full `uv run pre-commit run local-ci --hook-stage manual --all-files --verbose` passed on isolated Linux/arm64 at `ab7e8f777c1327c26a49fb269734dcf77db8f165`. All stages ran: frontend lint/type/tests/build, Python lint/type/tests, Rust checks and dependency audit, SQLite/PostgreSQL migrations, packaging, Docker build/scanning, Helm validation, and both kind smoke scenarios.
- The full run passed 1,643 frontend tests and 14,021 Python tests across the unit, integration, bridge, end-to-end, and PostgreSQL targets. Existing skips and the existing expected failure were unchanged; no test was filtered or weakened for this refresh.
- The focused route/client/unary suite passed all 63 tests. Live HTTP requests verified routed refusal on account A followed by upload success and durable ownership on B; pinned finalization called B only. TLS, ambiguous-request, and body-read failures each returned HTTP 502 with one upstream call.
- Pinned OpenSpec 1.11.0 strict validation of the owning `responses-api-compat` spec returned `valid: true`, with pre-existing warnings. Scoped Python diagnostics were clean.
- Native macOS execution exposed an unchanged capture-tool test assumption about `/tmp`; the successful full gate used the unmodified commit on Linux, matching GitHub CI's platform. Disposable databases, HTTP processes, and test infrastructure were removed after verification.

These tests inject failures at the routed transport boundary and use isolated test databases. They do not establish production deployment or real upstream-network behavior.
