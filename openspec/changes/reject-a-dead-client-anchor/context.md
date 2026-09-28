# Context

## Purpose and scope

Stop telling a client to retry a turn that can never succeed. In scope: the one remaining shape
of `previous_response_owner_unavailable` that production still shows. Out of scope: making an
account-bound body portable, which is not possible.

## Why this shape and not the planned one

The plan for this step was to decouple the account-neutral replay gate from the durable lookup,
on the theory that `no_durable_lookup` was the blocker. The attribution shipped in
`1.25.0-beta.8` measured it instead:

| reason | count / 20h |
|---|---|
| `account_scoped_input` | 9 |
| `payload_not_full_resend` | 1 |
| `no_durable_lookup` | 0 |

Zero. The planned work would have been effort spent on a case that does not occur. What the nine
are is visible one line earlier in the same logs: `fresh_reattach_full_resend_preserved` — a full
resend carrying the client's own anchor. That is the shape this change addresses, and the earlier
changes deliberately excluded it because the proxy may not silently discard an anchor the client
sent.

## Decisions

**Why `bridge_previous_response_not_found` and not `previous_response_not_found`.** The API layer
masks the bare code into a retryable `stream_incomplete`
(`api.py::_mask_previous_response_not_found_error`), and that mask is deliberate: most stale
anchors on this surface are the proxy's own injected bookkeeping, and surfacing them as an
invalid `previous_response_id` would blame the client for the proxy's state. The prefixed code
is the module's existing public answer for a proven-dead anchor it will not retry — the
`responses-api-compat` spec already carries a scenario for it — and it passes the mask. This
change reuses that contract rather than punching a hole in the masking rule.

**Why the retirement matters more than the status code.** The rejection alone would just be a
politer dead end. Retiring the owner is what makes the client's natural recovery — an
anchor-free resend — land on a healthy account through the machinery the previous change
already shipped.

**Why not the pre-submit marker.** Setting `_HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR` looked
harmless and was not: that provenance is what admits a failure to the raw-HTTP replay, so the
turn was sent to the plain upstream, which failed with `stream_incomplete` and hid this
rejection entirely. The first version of this change did exactly that and the end-to-end test
caught it.

## Failure modes

- **Rejecting a recoverable anchor.** The horizon rule is shared with the injected-anchor path:
  an owner returning inside the request budget is waited for, so a brief rate limit does not
  cost the conversation.
- **Masking.** Returning the bare `previous_response_not_found` is silently rewritten to
  `stream_incomplete` by the API layer; the test asserts the code the client actually receives,
  not the one the bridge raised, so a future refactor that reverts to the bare code fails here.
- **Double retirement.** `owner_retirement_attempted` is shared with the injected-anchor helper,
  so a request retires at most once whichever path it takes.

## Example

```text
# before
event=fresh_reattach_full_resend_preserved  bridge_kind=thread_header  account_id=<paused>
event=owner_unavailable_replay_rejected     detail=reason=account_scoped_input
proxy_error_response status=502 code="previous_response_owner_unavailable"
# ... the client retried this nine times in five seconds

# after
event=owner_unavailable_replay_rejected  detail=reason=account_scoped_input
event=dead_anchor_owner_retired          detail=outcome=report_bridge_previous_response_not_found
proxy_error_response status=404 code="bridge_previous_response_not_found"
# the client resends without the anchor -> served on a healthy account
```
