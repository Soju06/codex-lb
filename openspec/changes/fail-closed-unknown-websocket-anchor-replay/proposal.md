## Why

Codex sends `previous_response_id` on a direct WebSocket together with only the
incremental input: the items added after the previous request's input and the
previous response's output. The proxy keeps an anchorless "fresh" copy of an
anchored request so that it can recover when upstream answers
`previous_response_not_found` or the upstream socket closes before any output.
It may only do that when the request's input is a full resend, because the
copy is sent without the anchor.

`_websocket_client_previous_response_full_resend_is_retry_safe` decided this
with a structural check (more than one item, every tool output has its call in
the same input). It compared the input with stored context only when the anchor
was the response this connection last completed. For any other anchor it
returned `True`. Codex chains on anchors the proxy never records as completed:

- a response that ended `response.incomplete` with reason `interrupted` (for
  example a turn interrupted by new user input), which Codex treats as completed,
  while the proxy records continuity only on `response.completed`;
- an accepted `generate=false` prewarm, because empty prewarm completions are
  not recorded;
- any anchor from an earlier downstream socket or proxy process.

The delta that follows such an anchor (a user message, or a developer message
plus a user message) passes the structural check. When upstream then reported
`previous_response_not_found`, or the upstream socket closed before output, the
proxy replayed the delta alone without `previous_response_id`. Upstream answered
from the instructions, tools and the delta only, the client received a normal
completed response, and Codex chained its next turn on that response. Nothing
in the downstream stream shows that the thread's earlier context was missing.

## What Changes

- A client-supplied `previous_response_id` is replay-safe without its anchor
  only when this connection completed that response and the request's input
  restates the stored context prefix (the existing fingerprint check). Every
  other client-supplied anchor fails closed: no anchorless fresh body is kept.
- With no fresh body, a pre-output `previous_response_not_found` reaches the
  client as `previous_response_not_found`, and Codex retries the turn with its
  complete history. A pre-output upstream close replays the anchored request,
  which upstream either accepts or answers with `previous_response_not_found`.
- Proxy-injected anchors, verified HTTP bridge full resends and
  HTTP-to-WebSocket retries are unchanged.

Trade-off: a client that sends its complete history together with an anchor
that this connection did not complete no longer gets transparent recovery. It
receives `previous_response_not_found` and must resend without the anchor, as
it would against upstream directly. The proxy cannot distinguish that full
history from a delta, and a silent answer without context is worse than one
retry.

## Impact

- `app/modules/proxy/_service/websocket/helpers.py`
  (`_websocket_client_previous_response_full_resend_is_retry_safe`).
- Tests: `tests/unit/test_proxy_utils.py`,
  `tests/integration/test_proxy_websocket_responses.py`.
- No settings, schema or API changes.
