# Verification

Original verification base: `3987abfdf4a9cf372dde2f6694df2e4469bee602` (2026-09-09).

## Changed behavior

Recognized completed/failed/incomplete HTTP Responses terminals now release the
Rust HTTP exchange. Python consumes the final-fragment completion marker without
sending cancel. Persistent WebSocket lifetime and context-dependent errors retain
their previous owners. There is no throughput claim.

## Completed checks

- Canonical `make test-unit` (unit, simulation, request-log options API):
  **9,083 passed, 4 skipped, 1 expected failure**, 523.27 seconds. The expected
  failure is the repository's existing redundant reservation-release race canary.
- Rust workspace tests, formatting, Clippy with warnings denied, locked release
  helper build passed.
- Final release-helper integration selection: **332 passed**, covering native SSE,
  routed egress, and WebSocket event compatibility.
- Twelve new direct/routed × SDK/native × terminal-type cases prove complete
  delivery of fragmented terminals, upstream release without EOF, no Python
  cancellation command, and suppression of oversized trailing data.
- Release-helper terminal/cancellation subset: 16 passed, including isolation of
  another request on the shared helper after partial-stream cancellation.
- Native unit/shared-fixture selection: 148 passed.
- SDK/Responses/cancel-drain E2E selection: 23 passed.
- Built dashboard browser smoke: 5 passed.
- `make lint`, full `uv run ty check`, and 64 strict OpenSpec specs passed.

## Verification after updating to current main

The PR branch was rebased onto `2a4303492f2c1fd209a7490cf1493c98b56ffde1`,
including the subscription routing-hint and code-less upstream 429 fixes.
The only conflict joined independent additions to the outbound HTTP spec;
the implementation and regression tests were unchanged by the rebase.

- Real release-helper integration selection: **332 passed** in 24.99 seconds
  (`test_native_sse_egress.py`, `test_native_routed_egress.py`, and
  `test_native_websocket_events.py`).
- Adapter/routing/retry unit selection: **149 passed** in 16.79 seconds
  (`test_native_egress.py`, `test_http_subscription_routing_hint.py`,
  `test_responses_websocket_routing_hint.py`, `test_overload_backoff.py`, and
  `test_streaming_retry_virtual_time.py`).
- `openspec validate --specs --strict`: **64 passed, 0 failed**.

The full canonical unit run and other completed checks above were performed
before this rebase; the selections here were repeated on the updated branch.

## Broader suite and baseline comparison

The extra-dependency, work-stealing run of all unit tests completed with 9,023
passed, 3 skipped, and 3 failed. All three failures reproduce on the unmodified
beta.6 candidate (`38ae2f87817f4f7ed215d71360c806456ca084de`), whose `app/` and
`tests/` differ from this base only in the version constant:

- `test_stream_via_http_bridge_fails_closed_before_file_affinity_when_previous_response_owner_misses`
  relies on a previously created `file_account_pins` table without requesting a
  database fixture.
- Two `test_metrics.py` no-Prometheus cases patch `__import__`, while the existing
  implementation uses `import_module`. They fail when the optional real metrics
  dependency is installed.

These tests and production modules are unchanged by this slice. The canonical
`make test-unit` run uses the repository's normal dependencies and serial order
and passed as recorded above. An initial disk-backed parallel run was interrupted
during SQLite migration tests; complete runs used a task-owned temporary tmpfs
to avoid filesystem journal contention.

The final serialization adjustment omits the false completion marker from
ordinary events. Adapter unit tests were repeated (69 passed), and Rust tests,
Clippy, release build, formatting, and static validation were repeated after it.

Temporary PostgreSQL/Docker release-candidate probes belong to the deployment
preflight, not this change. No live upstream credentials or production traffic
were used for this slice's tests.
