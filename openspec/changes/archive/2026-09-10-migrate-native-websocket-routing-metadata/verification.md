# Verification

Original verification base: `dda902de6` (`origin/main` fetched 2026-09-10).

## Completed checks

- Broad proxy/WebSocket/HTTP bridge regression run: **1,729 passed**, 988.53
  seconds (`tests/unit/test_proxy_utils.py`,
  `tests/integration/test_proxy_websocket_responses.py`,
  `tests/integration/test_http_responses_bridge.py`).

- `make rust-check` using the existing scratch target: formatting, Clippy with
  warnings denied, 27 Rust tests and locked release helper build passed.
- Release-helper SSE/routed/WebSocket integration suite: **336 passed**.
  After adding explicit routing metadata assertions, the two affected files
  were repeated: **6 passed**.
- Native packaging/handshake and release-helper usage suite: **31 passed**.
- Native adapter and shared routing fixtures: **110 passed**.
- Routing fixtures, WebSocket terminal cancellation and virtual-time unit
  suites: **65 passed**.
- Initial adapter/client selection: **132 passed** (before expanded metadata
  failure cases; the adapter suite was repeated as recorded above).
- Full `make lint` (including architecture, cancellation safety, timing seams
  and settings-tier checks) and full `uv run ty check` passed.
- Strict change validation passed; **64** strict main specs passed both before
  and after archive.

## Contract evidence

- Shared `websocket-routing-v1.json` cases pin lifecycle ID precedence, failed
  model validation, Python whitespace, duplicate and escaped keys, surrogate
  handoff, negative/huge integers and noninteger rejection in both languages.
- `test_native_websocket_values_and_policy_match_python` compares actual native
  and opaque wire delivery with Python parsing, including bridge multiline
  framing, existing 1 MiB interpretation bounds and raw JSON values.
- `test_native_websocket_routing_preserves_delivery_watermarks` covers native
  and opaque transport crossed with downstream success/failure. Two pending
  requests retain distinct archive and response attribution; only the delivered
  request advances its sequence. A suppressed replay-created frame leaves the
  watermark intact, and a later non-advancing replay raises before delivery.
- `test_native_responses_websocket_preserves_interpretation_metadata` covers
  missing payload/ID/sequence plus invalid ID and sequence types. Each failure
  retires only its exchange; a peer still sends on the same helper, with exactly
  two connection requests and no replay.
- `test_bounded_event_queue_trips_on_bytes_or_events_and_releases_bytes_on_get`
  includes routing ID/sequence metadata in the queue byte budget and verifies
  that draining releases the charge.

No production traffic, upstream credentials or deployment were used. This
slice does not claim throughput gains or Rust ownership of pending queues,
request matching policy, retry, settlement or downstream acknowledgements.

## Broad-suite warning

The broad run emitted the Starlette deprecation warning and one aiosqlite
worker-thread `Event loop is closed` warning attributed to
`test_backend_responses_websocket_trusted_capability_routes_before_first_account_attempt[handshake]`.
No test failed. The database worker and fixture lifecycle were not changed by
this slice; this observation alone does not establish whether the warning also
occurs on the base commit.

The two variants of that test were repeated with
`-W error::pytest.PytestUnhandledThreadExceptionWarning`: **2 passed** in 4.05
seconds, with only the Starlette deprecation warning. The worker-thread warning
did not reproduce in this isolated check.

## PR base update

Rebased without conflicts onto `d2b2e7784` (current main, including the SQLite
invalidation rollback fix and unused Free-quota warmup change). Rust transport
and WebSocket implementation files did not overlap those changes. Repeated
release-helper native SSE/routed/WebSocket integration plus adapter and routing
fixture tests on this base: **446 passed** in 36.75 seconds. Strict OpenSpec:
**64 passed**. Full `make lint` and `uv run ty check` also passed again on
the updated base.

The full `make ci` command passed frontend lint and type checking, then its
frontend coverage process exited with code **143** before reporting test results.
The command had already exited when a local stop was attempted; the exact source
of the termination was not established. The host also lacks `kind`, which the
final Helm smoke target requires. This was not a full CI pass. Complete GitHub
Actions checks remain a PR gate.

## PR review corrections

CodeRabbit identified that a raw integer beyond Python's conversion limit could
fail the shared IPC decoder before attribution to an exchange. Rust now keeps
objects containing integer tokens over 640 digits opaque, including nested and
overwritten tokens. This bound covers Python's minimum configurable limit;
strings and floating-point tokens remain unaffected. The synchronized helper
and adapter rollout is explicit in the proposal, context and architecture docs.

- `make rust-check`: formatting, Clippy, **28 Rust tests**, and locked release build passed.
- Actual release-helper native SSE/routed/WebSocket integration plus adapter and
  routing fixtures: **447 passed** in 24.17 seconds. The new peer-isolation probe
  uses Python's 640-digit limit and tests 641- and 5,000-digit values at the
  top level, nested in arrays, and overwritten by duplicate keys. Both sockets
  then receive exact 640-digit sequences from the same helper process.
- Full `make lint`, `uv run ty check`, and **64** strict OpenSpec specs passed.
- The initial cloud CI failed on a duplicate SQLite index in the unrelated
  facet test. Main already contains the correction in PR #2296.
