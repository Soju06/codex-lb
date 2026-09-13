# Change: unify-affinity-observation-source

## Why

`persist-affinity-decision-on-request-logs` (#2352) landed the three
`request_logs` affinity columns and wired every emitter, but deliberately kept
`sticky_key_source` as "the existing resolved source classification" — four
independent if/elif ladders, one per transport, that had already drifted apart:

| policy | HTTP stream | compact | HTTP bridge | native WS |
|---|---|---|---|---|
| CODEX_SESSION from a client turn-state header | `session_header` | `turn_state_header` | `turn_state_header` | `turn_state_header` |
| CODEX_SESSION, no header present | `session_header` | `payload` | `session_header` | `session_header` |
| CODEX_SESSION from a proxy-synthesized turn state | `session_header` | `turn_state_header` | `turn_state_header` | `generated_turn_state` |

So the new column is not comparable across transports: the same continuity
signal is labelled differently depending on how the client connected, and the
HTTP stream path — the largest share of traffic — cannot report
`turn_state_header` at all. Any query that groups by `sticky_key_source` is
measuring the transport as much as the signal.

Two further cases mislabel the cohort the column exists to measure. All four
ladders decide `payload` vs `derived` from a boolean the caller reads off the
payload *before* resolution, but `_resolve_prompt_cache_key` writes the derived
key back onto the payload and rejects a blank client hint:

- `prompt_cache_key: "   "` is stripped, rejected, and a key is derived — but
  the pre-resolution boolean still saw a string, so the row says `payload`.
- When HTTP bridge setup falls back to `_stream_with_retry`, the second path
  recomputes that boolean from the payload the first path already mutated with
  its derived key, so a derived request is recorded as `payload`.

Both inflate `payload` at the expense of `derived` — the ~28% unkeyed cohort
whose cache behaviour motivated the column.

## What Changes

- `AffinityObservation.from_policy(policy, *, synthesized_turn_state=None)`
  now performs the classification itself, reading only the resolved policy.
  The four ladders are deleted. Because `_AffinityPolicy.codex_session_source`
  already records which signal the resolver used, the classification cannot
  disagree with the policy it describes the way header re-sniffing could.
- `_AffinityPolicy` gains `prompt_cache_key_source`, set by the two resolvers
  from `_resolve_prompt_cache_key`'s own return value. Routing never reads it.
  The `had_prompt_cache_key` locals in all four paths are deleted.
- `AffinityObservation.retaining_source(source, policy)` keeps the landed
  behaviour where a routing adjustment clears the key but the row should still
  report the signal the original attempt resolved (the HTTP bridge rebinds and
  the security-work replay). Those call sites are unchanged in meaning.
- The shape trace now reports the same source and kind as the persisted row,
  rather than computing its own from the same drifted ladder.

Out of scope: the columns, the migration, the emitter wiring, the listing
fields and the retention behaviour, all of which #2352 already landed.

## Capabilities

### Modified Capabilities

- `proxy-runtime-observability`: MODIFIED requirement "Durable affinity
  observation on request logs" — the sentence deferring to "the existing
  resolved source classification" is replaced by an explicit source domain,
  a single-shared-classification rule reading only the resolved policy, and a
  rule that the `payload`/`derived` verdict is recorded by the resolver rather
  than recomputed from the mutated payload. All six existing scenarios are
  kept verbatim; two are added.

## Impact

- Code: `affinity_observation.py`, `affinity.py`, `compact.py`,
  `streaming/retry.py`, `websocket/mixin.py`, `http_bridge/streaming.py`,
  `http_bridge/request_submit.py`.
- Schema: none. No migration, no new setting, no API change.
- Data: rows written before this change keep whatever their transport's ladder
  produced, so `sticky_key_source` is only cross-transport comparable from this
  revision forward. There is no backfill — the decision cannot be
  reconstructed from a persisted row.
