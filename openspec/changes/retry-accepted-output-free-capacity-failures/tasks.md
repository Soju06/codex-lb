# Tasks

## 1. Reproduction

- [x] 1.1 Add HTTP bridge integration tests where upstream accepts the turn (`response.created` + `response.in_progress`) and fails output-free with a capacity `error` or an abrupt close, asserting a retry on another account and exactly one `response.created` whose id the `response.completed` carries (fail on `main`).
- [x] 1.2 Add the same WebSocket integration tests, plus a sequenced variant proving the client's `sequence_number` keeps advancing across the suppressed replay prelude.

## 2. Classification and Eligibility

- [x] 2.1 Add the accepted-lifecycle-only predicate to `support.py` and generalize `_websocket_request_can_replay_before_visible_output` from created-only to lifecycle-only (sequenced watermark must cover exactly the prelude).
- [x] 2.2 Add `http_bridge/accepted_replay.py` with the accepted capacity classifier (capacity codes or the selected-model capacity message; refuses other pending requests, consumed replays, anchors without a retry-safe fresh payload, terminals naming another response, and terminals reporting output items or billed output/reasoning tokens) and delegate accepted states from `_websocket_precreated_retry_error_code`.

## 3. Single-Lifecycle Replay

- [x] 3.1 Add `suppress_next_in_progress_downstream`; suppress the replay's `response.in_progress` on both surfaces and exempt it from the WebSocket sequence-regression guard like the suppressed `response.created`.
- [x] 3.2 Stage accepted replays before `response_id` is cleared at the bridge capacity-wait and transparent-code branches and at the WebSocket transparent-code branch; preserve an already captured identity in `_prepare_websocket_request_state_for_visible_output_replay`.
- [x] 3.3 Re-claim the session response-create gate without waiting (bridge terminal and transport-close paths), mark work admission for re-acquisition, and exclude the failing account on the WebSocket surface.
- [x] 3.4 Let only terminal transport messages (close/error) replay an accepted turn in the bridge relay loop.

## 4. Verification

- [x] 4.1 Unit tests for the classifier, eligibility predicate, replay preparation, staging, gate re-claim, binary-frame guard, and mutants (output seen, billed output, other pending request, busy gate, second failure, quota codes, foreign response id).
- [x] 4.2 Run ruff, the proxy architecture check, the touched bridge/websocket unit and integration suites, and strict OpenSpec validation for this change.

## 5. Review Follow-ups

- [x] 5.1 Record model output on the direct websocket relay (`upstream_model_output_seen` for the shared `_MODEL_OUTPUT_EVENT_TYPES`) so an accepted turn whose upstream skipped `response.in_progress` is never replayed after a forwarded output item; mutant tests cover the capacity-error and abrupt-close paths.
