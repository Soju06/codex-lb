# Verification — 2026-09-11

Base commit: `e24b57c93` (origin/main when implementation started). No runtime
settings or production deployment were changed.

## Failing-before-the-fix proof

The `app/` diff was reverse-applied (`git apply -R`) with the test changes left
in place, the regression set was run, and the diff was restored and rerun. Seven
of the fifteen node ids fail without the `app/` change and pass with it; the
other eight pass in both worlds by design — they pin the two residual clauses and
the unchanged `image_generation` behaviour, so they must not be sensitive to the
fix. Mutation testing covers those eight instead (see "Residual-clause mutation
proof").

Failures on the unmodified tree:

- `tests/integration/test_http_promotion_accounting.py::test_historical_image_does_not_pin_later_turns_to_http`
  — `AssertionError: assert 'http' == 'auto'`
- `tests/integration/test_http_promotion_accounting.py::test_promoted_image_bypass_is_counted_without_pinning_http`
  — `AssertionError: assert 'http' == 'auto'`
- `tests/integration/test_proxy_responses.py::test_v1_responses_without_http_bridge_websocket_upstream_keeps_small_inline_images`
  — `AssertionError: assert 'http' == 'websocket'`
- `tests/unit/test_proxy_utils.py::test_stream_http_bridge_or_retry_keeps_unpinned_transport_for_small_input_image_when_bridge_disabled`
  — `AssertionError: assert 'http' is None`
- `tests/unit/test_proxy_utils.py::test_stream_http_bridge_or_retry_bypasses_bridge_for_input_image`
  — `assert [('retry', …, 'http')] == [('retry', …, None)]`
- `tests/unit/test_proxy_utils.py::test_http_downstream_small_input_image_follows_policy_under_always_websocket`
  — `AssertionError: assert 'http' == 'auto'`
- `tests/unit/test_proxy_utils.py::test_stream_retry_passes_image_generation_only_to_resolve_stream_transport`
  — `assert True is False`

Totals: **7 failed, 8 passed** without the `app/` change; **15 passed** with it.

The last failure is the one that proves both halves of the site-B edit are
load-bearing: it asserts on the `has_image_generation_tool` argument handed to
`_resolve_stream_transport`, which the sibling policy tests cannot see because
they stub that resolver out. A patch that narrowed only the explicit re-pin
would still fail it.

## Residual-clause mutation proof

The eight tests that pass in both worlds guard the two clauses the predicate
keeps, so each clause was disabled in turn and the guard set rerun.

- Size clause disabled: `test_oversized_image_request_still_pins_http` fails
  (`assert not upstreams`, the request opened an upstream WebSocket) and
  `test_stream_http_bridge_or_retry_forces_http_for_websocket_hostile_input_image[over_frame_budget]`
  fails (`assert None == 'http'`). Before the route test was pinned to an
  explicit `upstream_stream_transport = "websocket"` it survived this mutation,
  because under `"auto"` the frame-budget gate inside `_resolve_stream_transport`
  reaches the same outcome by itself.
- External-URL clause disabled:
  `test_external_image_url_request_still_pins_http`,
  `…forces_http_for_websocket_hostile_input_image[external_url]` and
  `test_http_downstream_external_image_url_still_forces_http_under_always_websocket`
  all fail.

## Passing checks

- `tests/unit/test_proxy_utils.py`: **1,400 passed**.
- `tests/unit/test_proxy_http_bridge.py`, `test_websocket_transport_fallback.py`,
  `test_http_bridge_safe_continuity.py`, `test_http_continuation.py`,
  `test_proxy_api_responses_contract.py`: **1,205 passed**.
- `tests/integration/test_proxy_responses.py` +
  `tests/integration/test_http_promotion_accounting.py`: **108 passed**.
- `tests/integration/test_http_responses_bridge.py`: **185 passed**.
- `uv run ruff format`, `uv run ruff check app tests`, `uv run ty check app`.
- `scripts/check_proxy_architecture.py`, `check_cancellation_safety.py`,
  `check_proxy_timing_seams.py`, `check_settings_tiers.py`.
- `openspec validate narrow-input-image-upstream-transport-pin --strict`;
  `openspec validate --specs` — **65 passed**.

Suite counts overlap and should not be summed as unique coverage.

## Corrections to the change design

- The design expected an oversized `input_image` request to record both the
  `payload_size` and the `image` bridge-bypass reasons. It records only
  `payload_size`: the size gate disables the bridge first, so the image gate's
  `runtime_config.enabled` guard is already false. That ordering is pre-existing
  and unchanged here, and the test asserts the observed behaviour.
- The design expected `test_v1_responses_without_http_bridge_http_upstream_preserves_historical_inline_artifacts`
  to fail before the fix. It is repointed at an explicit
  `upstream_stream_transport = "http"`, which makes it pass in both worlds —
  correct for its subject ("the HTTP upstream does not slim"), which should not
  depend on the transport pin at all.

## Review repairs

- Site A walked the whole input twice per request: `image_request` and then the
  predicate's own identical first check. Measured on this host with an 830 KiB
  text-only thread, one walk costs 2.66 ms against 5.87 ms for the
  `to_payload()` + `json.dumps` the size gate already runs, so the second walk
  was a ~45% add to that step on 100% of bridge-path traffic. `image_request`
  now short-circuits the predicate; no test monkeypatches
  `_responses_request_contains_input_image` on the façade, so the two seams
  cannot disagree.
- Precedence item 2 claimed the residual pins outrank an explicit
  `"websocket"` override unconditionally. Only site A does that. Site B keeps
  both clauses behind its pre-existing `not explicit_transport` gate, and a
  `/v1/chat/completions` request whose bridge admission already declined the
  bridge reaches site B without traversing site A. The text now scopes the claim
  to requests that reach the bridge routing decision; the runtime asymmetry
  predates this change and widening site B would be a separate behaviour change.
- The "external image URL still forces HTTP" scenario was unconditional while
  `_count_external_image_urls` reads only top-level `input_image` items and one
  level of `content`. Confirmed: a `function_call_output` whose `output` array
  holds an `https://` image returns `_responses_request_contains_input_image ==
  True` but `_count_external_image_urls == 0`, so the predicate returns `False`.
  Widening that traversal is out of scope, so the scenario now names the shapes
  the guard reads and the capability `context.md` records the bound.
- The bridge-bypass requirement reworded "raw HTTP Responses stream path" but
  left "the raw HTTP path is the source of truth" and one scenario's "raw HTTP
  stream path" behind, which re-introduced the bridge/transport conflation this
  change exists to remove. All three now read "raw (non-bridge)".

## Scope of evidence

Integration tests exercise the real `/v1/responses` route, real bridge and
admission code, and real request-log accounting with only upstream I/O faked. No
live upstream traffic was sent, and the production error-rate claim in #2363 is
not verifiable from this repository: `request_logs` carries no image marker, so
the image/non-image split is not derivable from LB telemetry. After deploy the
change shows up as a ratio shift on
`codex_lb_upstream_transport_decisions_total` (`policy="explicit"` falling,
`upstream_transport="auto"` rising) with
`codex_lb_http_bridge_routing_total{reason="image"}` flat.
