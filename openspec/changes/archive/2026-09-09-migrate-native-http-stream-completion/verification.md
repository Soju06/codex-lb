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

## Failure-path test evidence

The following exact tests were repeated together with the release helper on
`a365a1fd9f099a671af8db6da49e811760b1b120`: **21 passed** in 1.56 seconds.

- `tests/unit/test_native_egress.py::test_interpreted_sse_rejects_invalid_metadata_without_replay`:
  **15 passed**. The `events7` case ends an unfinished interpreted event before
  its final fragment; `events9` through `events14` reject invalid completion
  marker types, completion on an intermediate fragment, and completion on
  nonterminal event types. Every case verifies a protocol error, removal of the
  owned stream, and a single request sequence (no replay).
- `tests/unit/test_native_egress.py::test_bounded_event_queue_trips_on_bytes_or_events_and_releases_bytes_on_get`:
  **1 passed**. Both byte-budget and event-count overflow raise `QueueFull`;
  draining releases bytes, including interpreted-event text and metadata.
- `tests/unit/test_native_egress.py::test_client_close_does_not_hang_when_stream_queue_is_full`:
  **1 passed**. A stalled consumer exceeds the shared adapter's per-request
  byte budget, receives the bounded-queue transport error, and does not prevent
  a healthy request on the same helper from completing or client cleanup.
- `tests/integration/test_native_sse_egress.py::test_cancelling_native_stream_after_partial_event_keeps_peer_request_usable`:
  **4 passed** (direct/routed, with/without an already-cancelled scope). The real
  helper closes the cancelled upstream connection after a partial event while
  its peer finishes on the same still-running helper process.

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

## CI baseline update after the SQLite test fix

CI run `34341623522` on the earlier PR head `50681965d` completed its
integration-core-2 shard with 776 passed, 11 skipped, and one failure:
`test_realtime_call_location_drives_supported_account_bound_sideband_routes[current-app-uppercase-uuid]`.
The failure was `database is locked` while `TestClient` entered the application
lifespan and inserted the `hard_sticky_outage_grace_seeded` sentinel. It occurred
in the existing Realtime test's startup path, outside native HTTP Responses
completion. The six cases of that test passed in a local reproduction attempt.

Main then incorporated PR #2243, which moves blocking `TestClient` entry off the
async test loop and drains deferred SQLite writers. This PR was rebased onto
`88264ff8413ef1770cfec0aec516018fb23479bd` to include that fix. The rebase only
reconciled independent additions to the outbound HTTP spec; native completion
code and tests were unchanged. Checks repeated on the updated base:

- Real release-helper integration selection: **332 passed, 1 warning**,
  21.98 seconds (the same three native integration files listed above).
- `tests/integration/test_proxy_realtime_live.py`,
  `tests/integration/test_off_loop_test_client.py`, and
  `tests/unit/test_native_egress.py`: **109 passed, 3 warnings**, 61.17 seconds.
- Full `make lint`, `uv run ty check`, and strict OpenSpec validation passed
  (**64 specs**).

## Terminal-marker and error-handoff contract clarification

The requirement now explicitly mandates `stream_complete=true` on each
recognized terminal's final fragment. It also records the existing Python
ownership of SDK-dependent error normalization and termination. Completion code
is unchanged. In particular, a typeless error envelope remains nonterminal
without SDK-contract enforcement, but becomes `response.failed` with it.
Forcing Rust completion for both modes would break Python fallback parity.

Repeated release-helper checks: **32 passed, 1 warning**, 1.23 seconds:

- `test_native_stream_interpretation_matches_public_python_result`, filtered to
  `error`, `error_envelope`, `typeless_error`, `canonical_escaped_error_key`, and
  `data_only_escaped_error_key`: **20 passed** across direct/routed and SDK/native
  modes. Each sends a later recognized terminal and compares full output with
  the Python fallback.
- `test_native_http_terminal_releases_upstream_without_python_cancel`:
  **12 passed**, including mandatory true markers on final terminal fragments
  and omitted markers on preceding fragments.
- Strict OpenSpec validation: **64 passed**.

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
