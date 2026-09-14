# API Keys Context

See `openspec/specs/api-keys/spec.md` for normative requirements.

## Request-aware reservation estimate

The admission budget for token and `cost_usd` limits (Requirement
"Request-aware API-key usage reservations") sizes the input side from the
forwarded request payload: `min(utf8_length(serialized_payload_minus_caps), 8192)`.
Two implementation notes keep that value exact while avoiding redundant work on
the hot path:

- **Shared dump.** `ResponsesRequest.to_payload()` is deterministic, so the
  HTTP bridge prepare path computes it once and threads the same dict through
  client-metadata derivation, the forwarded `response.create` frame and
  `estimate_api_key_request_usage(payload, upstream_payload=...)`. The budget,
  frame bytes and input fingerprints are byte-identical to computing each stage
  from its own dump. When the prepare path rewrites the request (replayed
  side-effect tool-call dedupe under `previous_response_id`) the caller's dump
  is discarded and recomputed from the rewritten request, so the forwarded
  frame never carries un-deduped input. Callers that do not hold a dump keep
  the default single-argument form.
- **Early-exit serialization.** Because the estimate is capped at 8192 bytes,
  the estimator returns the cap as soon as it is proven: an `instructions`
  string of 8192+ characters alone suffices (a JSON string literal is never
  shorter than its character count), otherwise the payload is streamed through
  the same `sort_keys`/`ensure_ascii=False` encoder and stopped once 8192 bytes
  have been produced. Sub-cap payloads still yield the exact serialized length.
  The opaque-context checks (`previous_response_id`, `conversation`, file or
  image references) run before either shortcut, so the conservative `None`
  budget is unchanged.

Edge: a lone surrogate (`"\ud800"`) anywhere in the payload used to raise
`UnicodeEncodeError` (HTTP 500) from the full dump. It now raises only when it
sits in a chunk that is actually UTF-8 encoded, i.e. within the first ~8 KiB of
serialized output and not inside a string literal that the length shortcuts
(`instructions` >= 8192 chars, or a single chunk that alone covers the
remaining budget) prove the cap without encoding. Surrogates skipped that way
yield the 8192 cap like any other large payload.

## Queued steering accounting

The steering requirements in `spec.md` extend one successor reservation rather
than charging each correction as a separate response. Existing items reserve
only additional input; newly applicable limits use the ordinary bounded
request-admission and remaining-capacity policy. Actual successor usage is
settled once by the existing terminal path. This does not change pricing,
ordinary admission, or the current-window backfill performed by key updates.

Quota applicability can change while a socket remains open. For example, a
first steer can be unmetered, then an administrator adds an exhausted input
limit before a second steer. The second steer is rejected without retiring the
first; when capacity permits, its reservation is attached to the existing
successor rather than creating another settlement owner. Sender cancellation
is deferred across the first reservation's commit and attachment so socket
cleanup cannot miss a committed row. Rejection may remove an earlier input
while a later admitted input waits for transport handoff; only the earlier
increment is refunded.

Reservation adjustments lock the reservation before updating its limit rows.
Terminal claims retain their existing lock behavior but reload current item
values after claiming, including when the ORM session retains an older graph.
Failed refunds stay conservatively reserved for terminal reconciliation.
