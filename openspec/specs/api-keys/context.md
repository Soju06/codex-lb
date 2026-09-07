# API Keys Context

See [API key requirements](spec.md) for normative requirements.

## Daily reset schedule

The reset contract is defined in [Weekly token usage reset](spec.md#requirement-weekly-token-usage-reset).

Daily API-key limits follow the Ho Chi Minh City calendar using the operator-requested fixed UTC+7 offset. Persisted timestamps remain naive UTC; the host timezone does not affect calculation. Other limit windows retain their existing durations.

For example, a daily limit created at `2026-09-06 16:59 UTC` resets at `2026-09-06 17:00 UTC`, which is `2026-09-07 00:00` in Ho Chi Minh City. A daily limit created exactly at `17:00 UTC` receives the following day's `17:00 UTC` reset timestamp.

The existing leader-gated alignment pass runs at `16:50 UTC` (`23:50` local time), ten minutes before the reset boundary. It moves existing daily reset timestamps while preserving usage counters. Expired counters clear through request-time lazy expiry or the hourly background fallback; the alignment pass itself does not clear counters. Existing strict expiry comparisons process a limit once the clock is past its reset timestamp.

No database migration or host timezone change is needed. Fresh and explicitly reset limits use the local boundary immediately after deployment. Existing future timestamps converge at the next alignment pass, while expired timestamps converge when reset. Deploying after `16:50 UTC` can leave legacy timestamps until the next day's pass. Alignment logs identify `Asia/Ho_Chi_Minh` midnight and record the target as UTC.

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
