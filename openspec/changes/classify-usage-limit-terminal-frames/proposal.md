## Why

Upstream reports a spent account in two delivery forms. One is an HTTP body,
which carries a status and usually a code. The other is a serialized
`response.failed` frame, which carries no status and — documented, and common —
no error code at all. A code-less envelope normalizes to `upstream_error`, a
code that is in no transport retry list and in neither account-health code set.

Both streaming gates that read such a frame are pure code-table lookups, so the
sentence upstream did send is never consulted:

- the **retry decision** surfaces the rejection on the first account, while the
  identical rejection delivered as an HTTP body walks the pool;
- the **account-health write** leaves the account ACTIVE, while the identical
  *coded* frame benches it.

Measured on three accounts inside one request, the coded frame produced
`{a: rate_limited, b: rate_limited, c: rate_limited}` and the code-less frame
produced `{a: rate_limited, b: rate_limited, c: ACTIVE}`. The account that had
just said its window was spent came out the pool's healthiest and was selected
first next time. It bites the last account of a walk — reached with
`allow_retry` false, so its frame is terminal rather than retried — and,
separately, any frame that arrives after a lifecycle event is already
downstream.

The WebSocket transport has the same defect from the same cause, and it is the
transport most of the fleet's traffic uses. There the health write is also what
retires the socket and releases the turn for replay, so a code-less frame leaves
the spent account unbenched *and* still connected, and the next turn is handed
straight back to it.

## What Changes

- Read the usage-limit rejection from what upstream actually said: one message
  predicate (`is_upstream_usage_limit_message` /
  `is_upstream_usage_limit_rejection`), matched after folding non-alphanumeric
  runs to single spaces so punctuation and line wrapping cannot defeat it.
- The match does not depend on the HTTP status, because the same rejection
  arrives as a status-bearing body and as a status-less frame. It is allowed to
  decide only for `upstream_error`, the code a *missing* code normalizes to; a
  coded envelope keeps the class its code chose. `invalid_request_error` is
  deliberately left out — it is also upstream's catch-all for request-shaped
  failures, and the HTTP paths forward their status to the health write as
  evidence only, so a message arm there could bench a serving account on an
  echoed sentence.
- Classify a message-derived usage limit as `rate_limit` rather than
  `retryable_transient`, so the account is benched instead of being waited out
  on itself. As a consequence a code-less HTTP 429 whose message proves the
  usage limit is no longer treated as a burst rejection.
- Put the streaming retry gate, every streaming account-health gate, and the
  WebSocket replay-code and account-health gates on that one predicate, so the
  answer cannot differ by delivery form or by transport. A WebSocket frame whose
  sentence proves the usage limit answers under the `usage_limit_reached` code,
  so every later question about that turn gets the coded form's answer.
- Trigger the immediate coalesced usage refresh from the rejection rather than
  from the literal error code.

## Capabilities

### Modified Capabilities

- account-routing: the usage-limit rejection is recognized in every delivery
  form upstream sends it in, on both transports.
- usage-refresh-policy: the streaming usage-limit refresh trigger reads the
  rejection, not the literal code.

## Impact

`app/core/errors.py` (the message predicate), `app/modules/proxy/helpers.py`
(the rejection predicate and one classifier branch),
`app/modules/proxy/_service/streaming/{helpers,mixin,retry}.py` and
`app/modules/proxy/_service/websocket/{helpers,mixin}.py` (the gates). No
configuration, no schema, no new setting, and no change to the error code or
body surfaced to the client.
