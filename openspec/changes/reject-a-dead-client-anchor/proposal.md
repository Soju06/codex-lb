## Why

The three changes already shipped in `1.25.0-beta.8` removed ~99% of
`previous_response_owner_unavailable` from production: 20 hours after the deploy the code
appears **10 times**, against roughly a thousand for an equivalent window before it. The
attribution added by `trace-continuity-owner-replay-rejection` says what the remainder is, and
it is not what the plan predicted:

| `owner_unavailable_replay_rejected` reason | count / 20h |
|---|---|
| `account_scoped_input` | 9 |
| `payload_not_full_resend` | 1 |
| `no_durable_lookup` | **0** |

Every one of the nine carries `event=fresh_reattach_full_resend_preserved`: the client sent a
full resend **with its own `previous_response_id`**, and the projected body still holds
account-scoped state. `retire_continuity_owner_if_unavailable` deliberately skips that shape,
because the anchor names upstream state the proxy may not silently discard. So the turn fails —
correctly — but it fails as 502 `previous_response_owner_unavailable`, which says "retry later"
about an owner that is paused and not coming back. The client believes it: production shows one
thread retrying **nine times in five seconds**.

## What Changes

When the HTTP bridge fails because its continuity owner is unavailable, the anchor came from the
client, and that owner cannot return before the request's own budget expires, the proxy now
retires the owner and answers with the explicit rejection
**`bridge_previous_response_not_found`** (HTTP 404) instead of the retryable 502.

- **Why that code and not `previous_response_not_found`.** The API layer masks the bare code
  into a retryable `stream_incomplete` on purpose: an anonymous stale anchor is usually the
  proxy's own bookkeeping, and blaming the client's `previous_response_id` for it would be
  wrong. `bridge_previous_response_not_found` is the code this module already uses for a
  proven-dead anchor it will not retry, and it reaches the client unmasked. Its message tells
  the client to resend the history or start a new conversation.
- **The retirement is what closes the loop.** The client's recovery is an anchor-free resend,
  and an anchor-free request on a retired row is exactly what
  `retire-continuity-owner-on-the-request-path` already rebinds onto a healthy account. Without
  the retirement the resend would hit the same weld.
- **Same horizon rule as the injected-anchor path.** An owner returning inside the budget is
  waited for; rejecting its anchor would throw away recoverable continuity. One retirement
  attempt per request, shared with the injected-anchor path.
- **Not marked as a pre-submit failure.** That provenance is what admits a failure to the
  raw-HTTP replay; this is a terminal answer to the client, not a transport problem to route
  around. Marking it sent the turn to the plain upstream, which failed with its own error and
  hid this one.

- **The retirement call now answers "is this owner retired now".** It previously answered "did
  this call retire it", so the loser of a race between duplicate requests fell back to the
  retryable failure the winner had just replaced — and production shows this exact shape
  retrying nine times in five seconds. This also fixes the same race on the injected-anchor
  path shipped by `retire-continuity-owner-on-the-request-path`.

No schema change, no new setting, no ceiling-guarded file grows.

## Not in this change

- **Making an account-scoped body portable.** Encrypted reasoning and uploaded files are bound
  to their account; this change stops pretending otherwise, it does not move them.
- **The `no_durable_lookup` case.** Production reports it zero times, so the planned work to
  decouple the replay gate from the durable lookup is not justified by evidence and is dropped.

## Impact

- Affected specs: `responses-api-compat` (ADDED — the dead-owner client-anchor rejection).
- Affected code: `app/modules/proxy/_service/http_bridge/streaming.py`.
- Tests: `tests/integration/test_http_responses_bridge.py` — a client-anchored resume on a paused
  owner is rejected 404 `bridge_previous_response_not_found`, and the anchor-free retry that
  follows is served 200 on a healthy account.
- Partial fix for #1707.
