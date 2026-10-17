# Verification

## Scope and overlap

The implementation is based on `72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e`.
The only production change is the bounded managed HTTP close in `app/main.py`.
PR #2506 covers database-owning background tasks, #1443 covered audit/fleet
draining, and #2030 covered SQLite disposal. None supplies this HTTP close bound.
No configuration, dependency, schema or dashboard change is included.

## Requirements and scenarios

- The lifespan calls `_close_http_client_within(10)` at its existing HTTP close
  position. The helper uses a timed wait without cancellation.
- A retained task and done callback preserve cleanup ownership and consume
  late failures. Focused tests cover timeout, prompt completion, immediate and
  late errors, and self-cancellation.
- The actual lifespan regression covers prompt and stalled HTTP closure with
  both successful and failed database disposal. It asserts process-death
  marking, database disposal and absence of cancellation of the HTTP close.
- On the unmodified base, the two stalled-lifespan cases fail with a timeout.
  With the fix, the two lifecycle files report **116 passed**. The final command
  `uv run pytest -q tests/unit/test_graceful_shutdown.py tests/unit/test_otel.py
  tests/unit/test_native_egress_packaging.py` reports **123 passed**.

## Review and static validation

Independent review of the committed implementation found no actionable
findings. The canonical specification and context match the reviewed behavior;
the bound applies to this close step, not to every shutdown stage.

Focused Ruff and type checks passed. Both
`openspec validate bound-shutdown-http-client-close --strict` and
`openspec validate graceful-shutdown --type spec --strict` passed.

Global strict specification validation reports 17 failing pre-existing specs.
The failing specification IDs and issue data are identical to the unmodified
base; the affected graceful-shutdown specification passes.

## Full local gate

The required `uv run pre-commit run local-ci --hook-stage manual --all-files`
ran with isolated SQLite and PostgreSQL databases. Frontend checks, lint, type
checks, Rust checks and dependency audit passed. The Python unit/simulation
step reported **10486 passed, 2 failed, 99 skipped, 1 xfailed**.

One failure was a source-contract test looking for the old direct HTTP close
call. It now checks the bounded call at the same position after native helper
closure; the final 123-test focused run includes that corrected test.

The other failure, `test_output_under_a_temporary_filesystem_is_refused`, was
already reproduced on unmodified main on this macOS host. Later integration,
migration, package, container and Helm stages were not reached. These results
are not a fully green local gate; GitHub CI remains the integration gate.
