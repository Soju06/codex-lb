# Tasks: narrow-input-image-upstream-transport-pin

## 1. Implementation

- [x] 1.1 `app/modules/proxy/_service/response_create.py`: add the shared
  predicate `_input_image_request_requires_http_upstream(payload, *,
  payload_size_estimate_bytes)`, returning `True` only when the request carries
  an `input_image` part **and** either the estimate exceeds
  `_ws_transport_payload_budget_bytes()` or `_count_external_image_urls` finds a
  surviving external `http(s)` image URL.
- [x] 1.2 `app/modules/proxy/service.py` and
  `app/modules/proxy/_service/http_bridge/service_stubs.py`: expose the
  predicate through the façade seam both pin sites already use. The three
  `response_create` image predicates now share one plain `# noqa: F401`
  re-export block so `service.py` stays under its 2600-line architecture ratchet
  (it lands at 2599).
- [x] 1.3 **Pin site A** —
  `app/modules/proxy/_service/http_bridge/streaming.py`
  `_stream_http_bridge_or_retry`: replace
  `force_upstream_stream_transport = "http" if image_request else None` with the
  predicate, reusing the `payload_size_estimate_bytes` already computed for the
  bridge size gate. The bridge bypass, its `reason="image"` counter, and its log
  line are untouched.
- [x] 1.4 **Pin site B** — `app/modules/proxy/_service/streaming/retry.py`
  `_stream_with_retry`: stop folding `input_image` into
  `has_image_generation_tool=` (this argument, not the explicit re-pin below it,
  was the effective pin under `auto`), and gate the explicit re-pin on
  `image_generation_bypass or _input_image_request_requires_http_upstream(...)`.
- [x] 1.5 Sync `openspec/specs/responses-api-compat/spec.md` and its
  `context.md`, and update `docs/routing.md` so the published page no longer
  documents the removed pin. Precedence item 2 scopes the claim that the
  residual pins outrank an explicit `"websocket"` override to requests that
  reach the bridge routing decision, because site B keeps both clauses behind
  its pre-existing `not explicit_transport` gate; the external-URL scenario
  names the two shapes `_count_external_image_urls` actually reads.

## 2. Regression coverage (failing product path)

- [x] 2.1 `tests/integration/test_http_promotion_accounting.py::test_promoted_image_bypass_is_counted_without_pinning_http`
  — real `/v1/responses` route: an inline `data:` image still records
  `bypass/image` and `admission/smart_history` and still opens no upstream
  WebSocket session, but the raw call now carries `upstream_transport == "auto"`.
  The old parametrization is split so the `payload_size` leg keeps asserting
  `"http"`.
- [x] 2.2 `…::test_historical_image_does_not_pin_later_turns_to_http` — the
  reported production shape: the image is in the *first* turn and the last turn
  is plain text; the request must not be pinned.
- [x] 2.3 `…::test_oversized_image_request_still_pins_http` and
  `…::test_external_image_url_request_still_pins_http` — pin the two surviving
  clauses at the route so a later refactor cannot delete them silently. The
  oversize test pins `upstream_stream_transport = "websocket"` explicitly:
  under `"auto"` the frame-budget gate inside `_resolve_stream_transport` would
  satisfy the assertion on its own, so the test would have passed with the
  predicate's size clause deleted. Deleting either clause now fails both its
  route test and its unit test.
- [x] 2.4 `tests/integration/test_proxy_responses.py::test_v1_responses_without_http_bridge_websocket_upstream_keeps_small_inline_images`
  — route-level proof that an explicit operator `upstream_stream_transport =
  "websocket"` now wins for an image turn, and that the inline image reaches the
  upstream payload verbatim.
- [x] 2.5 `tests/integration/test_proxy_responses.py::test_v1_responses_without_http_bridge_http_upstream_preserves_historical_inline_artifacts`
  — repointed at an explicit `upstream_stream_transport = "http"`; its subject
  is "the HTTP upstream does not slim", and it was reaching that upstream only
  through the removed pin.
- [x] 2.6 `tests/unit/test_proxy_utils.py`: rename
  `…forces_http_for_input_image_when_bridge_disabled` to
  `…keeps_unpinned_transport_for_small_input_image_when_bridge_disabled` and
  assert the override is `None` (this is the population where the pin fired with
  no log and no counter); add
  `…forces_http_for_websocket_hostile_input_image` covering both residual
  clauses at site A; update the two override tuples in
  `…bypasses_bridge_for_input_image` to `None`.
- [x] 2.7 `tests/unit/test_proxy_utils.py`: rename the `always_websocket` image
  case to `test_http_downstream_small_input_image_follows_policy_under_always_websocket`
  (now `"auto"`), add
  `test_http_downstream_external_image_url_still_forces_http_under_always_websocket`,
  and close the coverage gap on the site-B argument with
  `test_stream_retry_passes_image_generation_{only_,tool_}to_resolve_stream_transport`
  — the sibling tests stub `_resolve_stream_transport` away, so nothing verified
  that argument before.

## 3. Verification

- [x] 3.1 Proved the key regressions fail on an unmodified tree: with only the
  `app/` diff reverse-applied, 7 of the 14 new/changed tests fail (`'http' ==
  'auto'`, `'http' is None`, `True is False`); with it restored, 14 pass.
- [x] 3.2 `uv run pytest tests/unit/test_proxy_utils.py` (1,400 passed),
  `tests/unit/test_proxy_http_bridge.py tests/unit/test_websocket_transport_fallback.py
  tests/unit/test_http_bridge_safe_continuity.py tests/unit/test_http_continuation.py
  tests/unit/test_proxy_api_responses_contract.py` (1,205 passed),
  `tests/integration/test_proxy_responses.py tests/integration/test_http_promotion_accounting.py`
  (108 passed), `tests/integration/test_http_responses_bridge.py` (185 passed).
- [x] 3.3 `ruff format` / `ruff check app tests` / `ty check app`, and the proxy
  architecture, cancellation-safety, timing-seam and settings-tier scripts.
- [x] 3.4 `openspec validate narrow-input-image-upstream-transport-pin --strict`
  and `openspec validate --specs`.
