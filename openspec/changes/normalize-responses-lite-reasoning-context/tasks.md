## 1. Characterize the invariant

- [x] 1.1 Add parameterized focused tests for the Lite reasoning finalizer covering omitted and null reasoning, absent/null/blank/differently-cased/other-string/non-string context, already-canonical idempotence, preservation of effort/summary/custom members, and a non-Lite no-op.
- [x] 1.2 Extend the existing raw HTTP Responses and trusted-versus-untrusted websocket marker tests to reproduce the missing `all_turns` invariant before implementation, while retaining the existing invalid non-object reasoning 400 behavior.

## 2. Normalize final Lite wire payloads

- [x] 2.1 Add one idempotent dictionary-level Responses Lite reasoning finalizer beside the existing body/header/marker helpers; require an already-derived canonical Lite disposition, set the exact `"all_turns"` value, preserve every sibling member, and never mutate the reusable request model.
- [x] 2.2 Apply the finalizer to raw Responses transport preparation before HTTP/websocket payload sizing and splitting, and to compact preparation before POST while preserving the existing input-only wire-budget validation, so normal websocket sends and automatic HTTP fallback share the invariant.
- [x] 2.3 Apply the finalizer to ordinary and size-guarded direct websocket `response.create` serializers plus HTTP bridge request/retry serializers, keying marker-only normalization only from already-canonical body-derived, bridge-preserved, or continuity-trusted metadata.
- [x] 2.4 Confirm fresh replay paths that sever `previous_response_id` linkage rebuild from an unmodified request model: marker-stripped non-Lite replays stay unnormalized, while replays whose own input retains `additional_tools` remain canonically signaled and normalized.

## 3. Prove every transport path

- [x] 3.1 Extend `tests/unit/test_proxy_utils.py` coverage for `test_stream_responses_derives_lite_http_header_from_additional_tools`, `test_stream_responses_uses_websocket_transport_and_marks_lite_payload`, the automatic websocket-upgrade HTTP fallback, and `test_compact_responses_derives_lite_http_header_from_additional_tools` to assert both the final signal and `reasoning.context`.
- [x] 3.2 Keep `tests/integration/test_proxy_responses.py::test_backend_responses_preserves_responses_lite_tools_and_outputs` as a pre-egress model/input-preservation test rather than asserting a wire-only mutation from its monkeypatched `core_stream_responses`; assert the raw final HTTP body in the core transport tests, and extend `tests/integration/test_http_responses_bridge.py::test_backend_responses_http_bridge_lite_request_omits_synthesized_tools` to assert canonical context on its actually serialized upstream body without losing tools, input order, or reasoning siblings.
- [x] 3.3 Extend the direct websocket base-Lite coverage and `tests/integration/test_proxy_websocket_responses.py::test_backend_responses_websocket_lite_marker_requires_previous_response_linkage` to prove trusted marker-only normalization and untrusted/stale marker no-op behavior.
- [x] 3.4 Retain and extend fresh-replay regression coverage for both marker-stripped trusted incremental replay and body-derived Lite replay, including ordinary and size-guarded serialization paths.

## 4. Verification and handoff

- [x] 4.1 Run the focused unit and integration nodes changed above, then run the relevant full proxy transport suites if the focused set passes.
- [x] 4.2 Run changed-file `uv run ruff check`, `uv run ruff format --check`, `uv run ty check`, `python3 scripts/check_proxy_architecture.py`, and `git diff --check`.
  - Changed sources and tests pass Ruff, format, scoped ty, the architecture checker, and `git diff --check`. The global ty run reports only four unresolved `_analytics` imports in pre-existing untracked `.codex/hooks/` files outside this change.
- [x] 4.3 Run `openspec validate normalize-responses-lite-reasoning-context --strict` and `openspec validate --specs`, then verify the implementation against this change before requesting current-head CI and Codex review.
- [ ] 4.4 Prepare one focused PR with `Fixes #1411`, no new settings or docs surface, and monitor upstream Lite invalid-request telemetry after deployment.
  - Draft PR #1431 is prepared with `Fixes #1411`; deployment and post-deployment telemetry monitoring remain pending.
