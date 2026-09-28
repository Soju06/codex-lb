# Context

## Incident evidence

The live aggregate was `codex-lb:live-stack-4d51de6` at source head
`4d51de672ed466147175f94625454645eb504454`. Request
`b81d9e10-59bd-47fb-b642-5f99dfe459d8` entered through the explicit HTTP bridge
route, selected one account, returned HTTP 200, and delivered 18 chunks totaling
111,687 bytes. At `2026-09-21T08:27:58Z` the downstream-delivery log recorded
`outcome=exception_before_terminal`, `terminal=None`, and `exc=ProxyResponseError`.
The traceback ended in `_normalize_public_responses_stream` while handling a
missing terminal event.

## Decision boundary

There are two different EOFs. A raw upstream EOF with no retry-exhaustion
marker remains the native abort lifecycle: the proxy cannot claim that the
upstream gave up in a retryable way. A retry layer that has exhausted its own
transport attempts has stronger evidence and emits an internal synthetic marker;
that marker is the handoff to the public native terminal renderer.

The public `rate_limit_exceeded` code is deliberately an egress translation.
The retry/account-health/request-log machinery continues to see and persist the
original transport code (`stream_incomplete`, `stream_idle_timeout`,
`upstream_request_timeout`, or `upstream_unavailable`). This prevents a client
reconnect hint from being mistaken for provider quota exhaustion.

## Worked path

1. `_stream_once` reports a transport failure after output is visible.
2. `_stream_with_retry` settles the account and yields one marked
   `response.failed` carrying the original code.
3. `_normalize_public_responses_stream` consumes the marker, emits one native
   retryable `response.failed`, and stops before forwarding `[DONE]`.
4. Native Codex receives a named failure and its own retry parser can honor the
   deterministic delay instead of treating the body as a silent disconnect.

The same marker path must also close the suspended stream wrappers. Without
that cascade, the retry generator's lease-release `finally` never runs and
repeated transport deaths wedge every account at `account_stream_cap` until a
process restart (the behavior reported in `Soju06/codex-lb#2470`).
