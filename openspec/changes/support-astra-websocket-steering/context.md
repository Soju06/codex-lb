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
