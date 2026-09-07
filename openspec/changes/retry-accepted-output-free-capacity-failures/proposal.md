# Retry Accepted Output-Free Capacity Failures

## Why

The bounded pre-visible retry only covers failures that arrive *before* upstream accepts a response. Native Codex traffic also fails after acceptance: upstream delivers `response.created` and `response.in_progress`, produces no output, and then terminates the turn with a capacity error (`server_is_overloaded`, `overloaded_error`, `model_at_capacity`, or the "selected model is at capacity" message) or drops the transport. Verified on `main`: the shared classifier (`_websocket_precreated_retry_error_code`) returns `None` as soon as `response_id` is set, and the transport-close eligibility (`_websocket_request_can_replay_before_visible_output`) caps the created-only case at one counted event, so created + in_progress is refused. The client gets a terminal error for a turn that produced nothing, and the agent stops mid-task.

This change takes over PR #1384 by Darafei Praliaskouski with the owner's narrowed scope: a native-Codex HTTP-bridge or WebSocket request that upstream accepted and then failed output-free is replayed once on another account while the client observes a **single** response lifecycle. The direct SSE initial-EOF retry from #1384 (which would reverse the #1389 fail-closed boundary) and anchored abrupt-close replay without an idempotency proof stay out of scope.

## What Changes

- Classify one accepted, output-free capacity terminal (`error` / `response.failed` with a capacity code or the selected-model capacity message, naming no other response and reporting no output items or billed output tokens) as replayable on both the HTTP bridge and the direct WebSocket surface. Quota and rate-limit codes after acceptance stay fail-closed.
- Widen pre-visible transport-close replay eligibility from "created only" to "accepted lifecycle only" (`response.created` plus optional `response.in_progress`, no output marker, no pending tool call, no buffered reasoning prelude); a sequenced watermark qualifies only when it covers exactly that prelude.
- Reuse the existing single-lifecycle mechanism (`replay_downstream_response_id` + created suppression + id rewriting) and extend it with `response.in_progress` suppression, so the client sees exactly one `response.created`, no duplicated `response.in_progress`, and every later frame under the id it already reads. The identity is captured **before** `response_id` is cleared; clearing first is the ordering hazard that made #1384's own tests observe two created ids.
- Re-claim the session response-create gate non-blockingly before an accepted replay owns the pre-created identity (a contended gate forwards the upstream terminal unchanged), mark shared work admission for re-acquisition, and exclude the failing account so the resend lands on another account when the body is account-neutral.
- A protocol-invalid binary frame never replays an accepted turn; only a terminal transport message (close or error) does.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: accepted output-free capacity failures are replayed within a single response lifecycle on the HTTP bridge and WebSocket surfaces; the HTTP bridge model-capacity wait applies before any downstream-visible *model output* rather than before any response event.

## Impact

- New helper module `app/modules/proxy/_service/http_bridge/accepted_replay.py` (accepted-state predicate, terminal classifier, output-evidence check, gate re-claim, replay staging).
- `app/modules/proxy/_service/support.py` (lifecycle-only predicate, `suppress_next_in_progress_downstream`, replay eligibility), `app/modules/proxy/_service/websocket/helpers.py` (classifier delegation, identity-preserving replay preparation), `app/modules/proxy/_service/http_bridge/upstream_events.py` and `app/modules/proxy/_service/websocket/mixin.py` (staging at the capacity branches, `in_progress` suppression, sequence-guard exemption, account exclusion), `app/modules/proxy/_service/http_bridge/request_submit.py` (gate re-claim on the transport-close path).
- No API, schema, migration, dependency, configuration, or dashboard changes. No ceiling file (`service.py`, `load_balancer.py`, `http_bridge/mixin.py`, `streaming/mixin.py`) grows.

## Known Limitations

- The HTTP bridge does not track a downstream `sequence_number` watermark, so when the replay's suppressed prelude is shorter than the failed attempt's, one SSE `sequence_number` may repeat on the bridge surface. The WebSocket surface keeps its fail-closed regression guard. Renumbering rewritten frames is a follow-up.
- A client that later anchors explicitly on the visible id receives upstream `previous_response_not_found` and enters the existing continuity recovery; proxy-injected anchors use the completed upstream id and are unaffected (same as the existing created-only replay).
- Excluding the failing account turns a transient overload in a single-account pool into a selection failure for that request (parity with the bridge's existing pre-created replay).
- The eventless pre-response watchdog stays disarmed for the replay attempt because `latency_response_created_ms` is already recorded (parity with the existing created-only replay); the generic receive timeout still bounds a silent replacement socket.
