# Enforce Astra configuration-update policy — change context

## Purpose / scope

Close the API-key reasoning bypass created by in-input
`configuration_update` items on subscription Astra. Codex-rs already
emits these items (`core/src/session/input_queue.rs`).

## Decisions

- Split from #2089. This change is only configuration-update + Ultra
  policy. Async tools and WebSocket steering stay out.
- Do not wait for #2085 catalog bootstrap. Policy keys off the model
  string `gpt-6-astra`.
- Do not relocate `resolve_wire_reasoning_effort`; #2085 edits the
  neighboring suffix list.
- Maintainer review at `ad588f61`: drop `_ASTRA_REASONING_EFFORTS`. The
  reference client sends `disabled` (persistent reasoning) and `none` on
  the wire; a local `{low, medium, high, xhigh, max}` set 400s those
  values even without an API key. Keep the string-type check and
  Ultra→Max aliasing; let upstream own the value set.
- Do not synthesize `medium` on allowed-list continuations. Limit
  `prepare_astra_reasoning_policy_continuation` to keys with
  `enforced_reasoning_effort`. Allowed-list keys rely on per-request
  validation so an omitted request-level effort matches the
  fresh-request path (no 403 from a fake medium, and no reset of a
  persisted in-history effort).
- Gate non-bridge HTTP `previous_response` trim on `gpt-6-astra` at the
  stream and collect sites and in `_prepare_http_fallback_payload`.
  HTTP-bridge internal trim may stay. Non-Astra direct HTTP bodies must
  remain untrimmed.

## Constraints

- Ultra and Max stay distinct for allow/enforce checks.
- Source-routed requests with the same model ID keep the source
  contract; API-key policy still applies.
- Compact endpoint rejects configuration updates.

## Failure modes

- A missing leading update on an anchored enforced-effort continuation
  would inherit an unseen prior effort. Preparation prepends one update
  for the enforced effort; repeated preparation is idempotent.
  Allowed-list continuations do not get a synthesized default.
- Mapping Ultra to Max during policy would let a Max-only key accept
  Ultra. Policy compares client-plane values.
- A source claiming Astra does not own a recorded subscription response.
  Resolve that ownership before choosing the WebSocket schema, so source-only
  controls such as top_logprobs cannot reach the subscription backend. Invalid
  requests fail before reservation; genuine source continuations still receive
  the existing HTTP-transport fallback. When direct WebSocket continuity has
  retained a complete body for stale-anchor replay, validate that body too;
  validating only the selected suffix can otherwise preserve a historical
  configuration update that the refreshed key no longer allows.

## Owner-precedence baseline

Upstream main at 5ad638b6 already routes recorded subscription anchors ahead of
model sources, but has no Astra schema policy on either transport. The new
schema requirement belongs to this change. At a67ffa4d HTTP correctly rejects
the owner-bound source-only control while WebSocket still connects upstream;
the earlier source-schema exemption left this new policy incomplete. This
repair closes that contract gap rather than redesigning source ownership.

The schema decision also fixes the ownership lookup result for that request.
If preparation finds no subscription owner for an Astra anchor, a concurrent
publication must not redirect the already prepared source payload to a
subscription socket. Reuse the request's existing lookup-outcome field for
that miss; the next request performs a fresh lookup. For example, an owner
published just after preparation's lookup leaves this turn on the HTTP
fallback, while the next turn validates `top_logprobs` against the subscription
schema. This applies to both new and reused WebSocket connections. An
8-case upstream control at 15ccd901 retains that fallback in the same ordering;
the extra lookup introduced with early schema selection exposed the race.

## Example

An enabled HTTP bridge keeps the original client body for continuation
bookkeeping and prepares a separate copy for dispatch. If session creation
fails before submission, the eligible raw-HTTP retry must prepare that original
body again, using the same trim-then-validate order as a disabled bridge. For
example, an anchored Low request without usage limits must forward a leading
Low update even after a WebSocket connect failure. This completes the new
continuation policy across the existing fallback; it does not change which
failures are eligible for replay or who owns a usage reservation.

A policy reset inserted after an operation-ledger anchor
belongs to the forwarded request, while continuation fingerprints describe the
client's history. Keep those representations separate: a later full resend does
not contain the proxy's reset. The late anchor helper updates the wire payload
and its existing usage estimate while retaining the already captured client
count and fingerprint. This extends the existing client-prefix contract to late
anchors without changing the quota cap, settlement policy, or source-owned schema.

Effort serialization must retain the rest of each reasoning mapping. Source
forwarding uses the original body, while API-key admission also uses the
serialized representation for its existing bounded input estimate. Dropping a
large source-specific reasoning value from that representation would reserve
too little for overlapping requests even though the source receives the full
value. Copying the reasoning mapping and changing only effort preserves this
contract without changing the budget cap or remaining-quota calculation.

Source admission now measures the prepared source-bound body through the
existing estimator. The subscription serializer is not that body: for example,
a source effort with leading whitespace remains intact on source forwarding
but shrinks during subscription normalization. The same source-route probes
retain their budget on upstream main, while the new normalization undercounts
them. Passing the prepared body also keeps source overrides and tool filtering
inside the existing estimate, before a reservation is acquired. Its cap,
remaining-quota and settlement rules do not change.

Subscription update shape validation precedes update key-policy checks, so an
unsupported effort returns the promised 400 even on a restricted owner-bound
continuation once its existing request-level and continuation policy permits
processing the explicit updates. A valid but forbidden effort still returns 403. This ordering is
limited to subscription configuration updates; the baseline's request-level
policy ordering and source-owned schemas remain intact.

Key allows `low` only. Request input contains
`configuration_update` with `high`. The proxy returns
`reasoning_effort_not_allowed` before any upstream send.

## Related

- Split from #2089. Catalog: #2085.
