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

## Constraints

- Ultra and Max stay distinct for allow/enforce checks.
- Source-routed requests with the same model ID keep the source
  contract; API-key policy still applies.
- Compact endpoint rejects configuration updates.

## Failure modes

- A missing leading update on an anchored restricted-key continuation
  would inherit an unseen prior effort. Preparation prepends one allowed
  update; repeated preparation is idempotent.
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

Effort serialization must retain the rest of each reasoning mapping. Source
forwarding uses the original body, while API-key admission also uses the
serialized representation for its existing bounded input estimate. Dropping a
large source-specific reasoning value from that representation would reserve
too little for overlapping requests even though the source receives the full
value. Copying the reasoning mapping and changing only effort preserves this
contract without changing the budget cap or remaining-quota calculation.

Key allows `low` only. Request input contains
`configuration_update` with `high`. The proxy returns
`reasoning_effort_not_allowed` before any upstream send.

## Related

- Split from #2089. Catalog: #2085.
