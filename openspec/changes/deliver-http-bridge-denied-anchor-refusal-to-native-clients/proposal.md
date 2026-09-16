## Why

The HTTP bridge fails a request closed when it is about to dispatch a
`previous_response_id` the proxy injected and upstream has already denied. The
refusal is raised from inside the SSE body generator as a 502 carrying the
spec-mandated public code `stream_incomplete`.

For a native Codex client that code is also how an observed upstream transport
failure ends, and the native lifecycle answers a transport failure by ending the
downstream stream with no terminal event. The proxy therefore classified its own
pre-dispatch refusal as an upstream transport failure and aborted the committed
body: the client received HTTP `200` with zero bytes, no terminal event, and
never saw the "retry the request" message, while the ASGI layer logged an
unhandled exception and the delivery trace recorded
`outcome=exception_before_terminal chunks=0 bytes=0` (issue #2364). Non-native
clients were unaffected — they already receive the 502 JSON envelope.

## What Changes

- Carry provenance on `ProxyResponseError` (`local_pre_dispatch_refusal`) so the
  API layer can tell a refusal the proxy raised before any upstream frame was
  sent from a transport failure it observed, without changing the public error
  code. Re-coding the refusal was rejected: the bridge's explicit
  previous-response-rejection predicates would then turn a deliberate
  fail-closed into a local recovery/replay attempt.
- Flag the denied-anchor before-dispatch fence with it. The status, the code,
  the message, and the `continuity_fail_closed` record are unchanged.
- Exclude flagged errors from the native transport-failure lifecycle on both
  paths: the startup-probe branch returns the ordinary HTTP error response, and
  the already-committed branch yields a terminal `response.failed` that is not
  stamped as a synthetic transport failure.
- Propagate the flag through `_OwnerForwardRequestError`, which rebuilds a
  `ProxyResponseError` from a fixed field list.
- Carry the flag across the internal bridge owner forward. In a ring deployment
  the session, its denial tombstone, and the fence live on the owner replica, so
  a continuation reaches the fence only through `/internal/bridge/responses`.
  The origin rebuilds the owner's failure from the status and the body alone,
  and that body cannot express the difference between a refusal the owner raised
  before dispatch and a transport failure it observed. The owner therefore marks
  its error response with `x-codex-bridge-local-pre-dispatch-refusal` and the
  relay reads it back onto the rebuilt error. Without it the fix would simply
  move the empty 200 from the owner onto the origin. A non-200 alone is not
  taken as proof: it does not establish that the owner sent no upstream frame.

## Capabilities

### Modified Capabilities

- responses-api-compat: the native transport-failure lifecycle applies only to
  failures observed on the upstream leg, and the denied-anchor before-dispatch
  fail-closed must reach the client.

## Impact

`app/core/clients/proxy.py`, `app/modules/proxy/api.py`,
`app/modules/proxy/_service/http_bridge/request_submit.py`,
`app/modules/proxy/_service/http_bridge/owner_forwarding.py`, and
`app/modules/proxy/http_bridge_forwarding.py`, plus bridge-route, owner-forward,
and stream-shaping regression coverage.

Native Codex clients now see a real error where they previously saw a truncated
stream, which may change their retry behaviour. That is already the precedent on
the same route: a pre-response 429 reaches a native client as a real HTTP status
with `Retry-After`, and every error code outside the four-code transport set
already reaches native clients as a JSON error.

Scope is the single raise site. The sibling local refusals in
`_service/http_bridge/` that also use codes from the transport set keep today's
behaviour — some of them are not provably pre-dispatch, so a blanket sweep would
be wrong; the new field is the seam a follow-up uses for the safe subset. The
spec text is scoped to refusals the proxy records as such for the same reason,
so it does not promise delivery the code does not implement. The nearest
unrecorded neighbours are the bridge retry-circuit suppressions
(`request_submit.py` `_http_bridge_precreated_retry_block` and the stale-anchor
replay generation check), which raise `upstream_request_timeout` and still reach
a native client as a terminated stream when the turn carries a continuation
anchor.

Multi-replica deployments have a rolling-upgrade window. An upgraded owner
answers the internal forward with a real 502 where it used to abort a committed
200, and an origin that predates this change ignores the marker and applies its
own native transport lifecycle, so a forwarded denied-anchor refusal reaches the
client as an empty 200 until every replica is upgraded. Today the same case ends
as a `bridge_owner_unreachable` 503, because the origin sees the owner's
truncated body as a transport failure. Single-instance deployments — the default
— never take that path, and the window closes as soon as the origins are on this
build.

The anchor-poisoning loop itself is untouched. When the durable clear returns a
clean no-match the tombstone persists and the durable row re-injects the same
id, so every retry is refused again, exactly as the retirement requirement
mandates. After this change the user sees repeated visible errors instead of
repeated silent truncations.
