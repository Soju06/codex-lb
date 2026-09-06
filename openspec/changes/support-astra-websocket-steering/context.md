# Support Astra WebSocket steering — change context

## Purpose / scope

Let an owned Astra Responses WebSocket submit `response.steer` without
opening a new upstream connection.

## Decisions

- Split from #2089. Configuration-update and async tools are sibling PRs.
- rust-v0.153.4 / openai/codex do not emit `response.steer`.

## Constraints

- Steering input is type + previous_response_id + nonempty user input.
- Failed refund of a rejected steer must not kill the socket.

## Failure modes

- Releasing the placeholder before prepare succeeds drops the
  continuation if prepare then fails.
- FOR UPDATE on every finalize/release was rejected as out of scope.

## Example

Client sends `response.steer` against `resp_1` with a user correction.
The proxy admits it on the same socket, queues it on one successor
reservation, and records successor usage once.

## Related

- Split from #2089. Slices: #2097 (a), #2099 (b).

## Review repair

Final steering rejection uses the same socket-owned retired reservation collection as explicit replacement, including across upstream reconnects. Before a rejected unsent explicit request is released, its exact control-map entry is removed under the pending lock so a corrected request can retry. These are lifecycle repairs within the existing steering contract; no routing or policy expansion is introduced.

A final rejected steer drops the active continuation but retains its parent ID on the upstream control. Without that correlation, a delayed automatic successor falls into generic FIFO and steals unrelated admission and accounting. Only the ID is retained for the connection lifetime, not the released request or payload. Current explicit and steering requests for the parent take precedence; the retained ID protects otherwise unmatched late successors.

Registration alone does not allow an explicit replacement to receive an automatic successor's ID. Until response_create_sent_at is set at the dispatch boundary, matching suppresses that automatic lifecycle and preserves the explicit payload/reservation. The same guard applies after final rejection has converted the replacement into an ordinary anchored pending request. Deterministic WebSocket tests cover placeholder-release and account-cap waits, with and without a preceding final rejection.
