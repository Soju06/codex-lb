## ADDED Requirements

### Requirement: Source-routed limited-key streams are live and settle at an estimate when usage is missing or the client cancels after the first output item

For Responses requests served by an OpenAI-compatible model source, the proxy SHALL stream live to the client for every API key, including keys with token or cost limits: no byte of the source stream MAY be withheld until the source's terminal frame arrives. This is a scoped exception to the rule that a limited key's source-routed reservation is finalized only from authoritative upstream `usage`, and it applies to the Responses stream route only; non-stream source-routed Responses MUST keep failing closed with HTTP `502` and error code `usage_unavailable` when the JSON body lacks usable usage, and chat completions keep their buffered limited-key accounting. When a limited key's source stream completes without usable `usage`, the reservation MUST be finalized at an estimate and MUST NOT be released or finalized at zero: the input figure is the request's admission estimate (or the reservation default of 8192 tokens when none exists) and the output figure is `max(2048, delta_chars // 4)` where `delta_chars` sums the `delta` lengths of every `*.delta` frame the source produced. When the client disconnects after the source produced its first output item, a limited key's reservation MUST likewise be finalized at that estimate; a disconnect before the first output item MUST release the reservation. An estimate MUST NOT be written to the request-log row as token usage; the row keeps `null` usage and the settlement is visible through a WARN log line (`source_usage_missing_settled_at_estimate`) and the `codex_lb_model_source_usage_estimated_total{source_id,cause}` counter with `cause` `missing_usage` or `client_cancel`. A stream that completes with usable `usage` MUST be finalized from that usage with the source's per-model cost, and a cancelled stream whose terminal frame already carried usage MUST be finalized from that usage. Keys without applicable limits are never estimated: their reservations are released. A stream the source ends with a failure terminal (`response.failed` or `error`) produced no answer: the attempt MUST be recorded as `error` with error code `model_source_response_failed` and the reservation MUST be released, never estimated, even for a limited key. A stream the source closes without any terminal frame produced no answer either — the client receives a failure (the public wrapper synthesizes `response.failed` with code `upstream_stream_truncated` for SDK clients) — so the attempt MUST be recorded as `error` with error code `model_source_stream_truncated` and the reservation MUST be released, never estimated. The settlement layer decides from the client-visible outcome: a success terminal that reached the client but outgrew the frame parser's cap is a delivered answer and MUST stay a `success` (settled at the estimate for a limited key), while a success terminal the source produced but the public wrapper had to rewrite into `response.failed` (a `response` that is not an object, invalid output items) reached the client as a failure: the attempt MUST be recorded as `error` with error code `model_source_response_invalid` and the reservation MUST be released, never estimated. A settlement that fails MUST fall back to a release and record the attempt as `error` with error code `usage_settlement_failed`.

#### Scenario: Limited key receives the first output item before the terminal frame

- **GIVEN** an API key with a token limit assigned to a Responses-capable model source
- **AND** the source emits `response.created`, `response.output_item.added` and a text delta, then pauses before `response.completed`
- **WHEN** the key streams a Responses request routed to that source
- **THEN** the client receives the output item frame while the source is still paused
- **AND** after the source completes with `usage`, the reservation is finalized with the source's input and output tokens and its per-model cost

#### Scenario: Missing usage settles at the estimate

- **GIVEN** an API key with a token limit and a Responses request whose admission estimate is 1234 input tokens
- **WHEN** the source stream completes without a usable `usage` object after producing 20000 delta characters
- **THEN** the reservation is finalized with 1234 input tokens and 5000 output tokens
- **AND** the request-log row records status `success` with `null` usage
- **AND** a `source_usage_missing_settled_at_estimate` warning and a `missing_usage` estimate count are emitted

#### Scenario: Client cancels after the first output item

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the client disconnects after `response.output_item.added` was relayed and before the terminal frame
- **THEN** the reservation is finalized at the estimate with cause `client_cancel`
- **AND** the request-log row records status `cancelled`

#### Scenario: Client cancels before the first output item

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the client disconnects after `response.created` and before any output item
- **THEN** the reservation is released
- **AND** the request-log row records status `cancelled`

#### Scenario: Failure terminal is never charged

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the source emits `response.created` and then `response.failed` without usage
- **THEN** the client receives the relayed failure and the reservation is released
- **AND** the request-log row records status `error` with error code `model_source_response_failed`

#### Scenario: Clean EOF without a terminal is never charged

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the source emits `response.created`, `response.output_item.added` and a text delta, then closes the body cleanly without `response.completed`
- **THEN** the client receives the synthesized `response.failed` with code `upstream_stream_truncated`
- **AND** the reservation is released and the request-log row records status `error` with error code `model_source_stream_truncated` and `null` usage

#### Scenario: Success terminal rewritten into a failure is never charged

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the source emits `response.created`, `response.output_item.added` and a `response.completed` whose `response` is not an object
- **THEN** the client receives the public wrapper's `response.failed` with code `invalid_json`
- **AND** the reservation is released and the request-log row records status `error` with error code `model_source_response_invalid` and `null` usage

#### Scenario: Oversized success terminal stays a delivered answer

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the source's `response.completed` frame outgrows the usage parser's frame cap but is relayed to the client intact
- **THEN** the request-log row records status `success`
- **AND** the reservation is finalized at the estimate with cause `missing_usage`

#### Scenario: Non-stream keeps failing closed

- **GIVEN** an API key with a token limit
- **WHEN** a non-stream source-routed Responses request returns JSON without usable `usage`
- **THEN** the response is HTTP `502` with error code `usage_unavailable`
- **AND** the reservation is released
