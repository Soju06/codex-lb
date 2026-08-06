## 1. Spec

- [x] 1.1 Add the `responses-api-compat` delta requirement: a proxy-injected durable anchor is cleared, fenced by owner epoch, when its `response.create` hits the eventless `missing_response_created_timeout` **and** the client's own payload already looked like a full conversation resend; a client-supplied anchor, or a genuine delta-only proxy-injected anchor, is never cleared by this path.
- [x] 1.2 Document the load-bearing assumption (delta-only proxy-injected anchors are explicitly out of scope, pending maintainer input) in `design.md` and the proposal's Non-Goals.

## 2. Implementation

- [x] 2.1 Add a fenced `DurableBridgeRepository` write (`clear_latest_response_anchor`) that nulls `latest_response_id`, `latest_input_item_count`, `latest_input_full_fingerprint`, and `latest_pending_tool_calls_json` for a session, scoped to `owner_instance_id`/`owner_epoch` (reuses the `_execute_fenced_session_update` shape); leaves `latest_turn_state` and aliases untouched.
- [x] 2.2 Add the matching `DurableBridgeSessionCoordinator.clear_live_session_response_anchor` wrapper.
- [x] 2.3 Add `request_state.proxy_injected_anchor_had_full_resend_payload`, capturing `_http_bridge_payload_looks_like_full_resend` at every anchor-injection site in `streaming.py` (durable fresh-reattach, owner-forward interrupted-tool-call recovery, session-level injection) and propagating it through the later trim re-prepare step that reconstructs `request_state`.
- [x] 2.4 In the HTTP bridge upstream reader's `missing_response_created_timeout` handling (`_relay_http_bridge_upstream_messages`), detect whether any request whose eventless deadline actually expired carries **both** `proxy_injected_previous_response_id = True` **and** `proxy_injected_anchor_had_full_resend_payload = True`, and if so, invoke the new clear before `_fail_http_bridge_reader_and_maybe_retire` releases durable ownership.
- [x] 2.5 Emit a structured, low-cardinality `durable_anchor_invalidated` bridge event alongside the existing `missing_response_created_timeout` / stuck-retire telemetry.
- [x] 2.6 Deliberately did not reuse the existing `fresh_upstream_request_is_retry_safe` field for this gate after tracing it end to end and confirming it answers a stricter, different question (see `design.md` Decisions) that is always `False` at the point this gate needs to fire, including for the confirmed #1534 reproduction.

## 3. Coverage

- [x] 3.1 Unit-test the new repository/coordinator write in `tests/unit/test_durable_bridge_sessions.py`: clears all four columns when fenced correctly and leaves `latest_turn_state` and the previous-response alias row untouched; is a no-op (current owner's state intact) when the owner epoch has advanced past the caller's epoch.
- [x] 3.2 HTTP-bridge regression in `tests/unit/test_proxy_http_bridge.py`: a proxy-injected-anchor owner with `proxy_injected_anchor_had_full_resend_payload = True` that hits `missing_response_created_timeout` triggers the fenced durable clear with the session's `durable_session_id`/`durable_owner_epoch`. Combined with 3.1's proof that a cleared row's `lookup_request_targets` returns `latest_response_id = None` (the sole input `streaming.py`'s `fresh_reattach_can_use_durable_anchor` reads), this establishes that the next reattach on that durable session no longer injects the stale anchor.
- [x] 3.3 Regression proving a **delta-only** proxy-injected anchor (`proxy_injected_previous_response_id = True`, `proxy_injected_anchor_had_full_resend_payload = False`) that hits the same timeout does not invoke the clear.
- [x] 3.4 Regression proving a client-supplied `previous_response_id` reattach (`proxy_injected_previous_response_id = False`) that hits the same timeout does not invoke the clear.

## 4. Verification

- [x] 4.1 Run focused durable-bridge, HTTP-bridge, replay-safety, streaming-timeout, and full `tests/unit` regression suites, plus `tests/integration/test_http_responses_bridge.py` (112 passed).
- [x] 4.2 Run Ruff, formatting, changed-file `ty` type checks, and the proxy architecture checks — all pass.
- [x] 4.3 `openspec validate invalidate-durable-bridge-anchor-after-stuck-timeout --strict` and `openspec validate --specs` — both pass.
- [x] 4.4 Reviewed the final diff for fencing correctness (no unfenced writes), scope creep beyond the four anchor columns, and account/turn-state/alias leakage — none found.

## 5. Sequencing

- [ ] 5.1 Push this change as a proposal-forward PR (spec + working implementation for the confirmed-safe, full-resend-gated subset) and ask the maintainer to confirm the delta-only scope boundary before considering any follow-up that would need to extend coverage to that shape.
