## ADDED Requirements

### Requirement: Source-routed limited-key streams are live and settle at an estimate when usage is missing or the client cancels after delivered content

For Responses requests served by an OpenAI-compatible model source, the proxy SHALL stream live to the client for every API key, including keys with token or cost limits: no byte of the source stream MAY be withheld until the source's terminal frame arrives. This is a scoped exception to the rule that a limited key's source-routed reservation is finalized only from authoritative upstream `usage`, and it applies to the Responses stream route only; non-stream source-routed Responses MUST keep failing closed with HTTP `502` and error code `usage_unavailable` when the JSON body lacks usable usage, and chat completions keep their buffered limited-key accounting. When a limited key's source stream completes without usable `usage`, the reservation MUST be finalized at an estimate and MUST NOT be released or finalized at zero: the input figure is the request's admission estimate (or the reservation default of 8192 tokens when none exists) and the output figure is `max(2048, delta_chars // 4)` where `delta_chars` sums the `delta` lengths of every `*.delta` frame the source produced. When the client disconnects after content was delivered to it — an event frame carrying an output item, a `*.delta`, a success terminal or any other content-bearing event was handed by the settlement layer to the transport — a limited key's reservation MUST likewise be finalized at that estimate; a disconnect before any content was delivered MUST release the reservation, even when the frame parser had already observed `response.output_item.added` in bytes still withheld ahead of a pre-content hook, and even when the source stream body had already flushed content that the public wrapper then dropped (an event outside `response.*` / `error` under the SDK contract) or parked ahead of `response.created`: delivery is decided from the event frames handed to the transport, never from the parser's observation or the body's flush point. A client that disconnects after the success terminal was relayed received the whole answer: the attempt MUST be recorded as `success` and settled exactly like a stream that ran to the source's EOF. An estimate MUST NOT be written to the request-log row as token usage; the row keeps `null` usage and the settlement is visible through a WARN log line (`source_usage_missing_settled_at_estimate`) and the `codex_lb_model_source_usage_estimated_total{source_id,cause}` counter with `cause` `missing_usage` or `client_cancel`. A stream that completes with usable `usage` MUST be finalized from that usage with the source's per-model cost, and a cancelled stream whose terminal frame already carried usage MUST be finalized from that usage. Keys without applicable limits are never estimated: their reservations are released. A stream the source ends with a failure terminal (`response.failed` or `error`, including a data record without a `type` field that carries an `error` object, which the public wrapper classifies as the `error` terminal) produced no answer: the attempt MUST be recorded as `error` with error code `model_source_response_failed` and the reservation MUST be released, never estimated, even for a limited key; the settlement layer MUST classify such a record the same way when it decides from the relayed frames, so a native client that tears the stream down on it is recorded as that error and never as a cancel. A stream the source closes without any terminal frame produced no answer either — the client receives a failure (the public wrapper synthesizes `response.failed` with code `upstream_stream_truncated` for SDK clients) — so the attempt MUST be recorded as `error` with error code `model_source_stream_truncated` and the reservation MUST be released, never estimated. The settlement layer decides from the client-visible outcome: a success terminal that reached the client but outgrew the frame parser's cap is a delivered answer and MUST stay a `success` (settled at the estimate for a limited key), while a success terminal the source produced but the public wrapper had to rewrite into `response.failed` (a `response` that is not an object, invalid output items) reached the client as a failure: the attempt MUST be recorded as `error` with error code `model_source_response_invalid` and the reservation MUST be released, never estimated. The client-visible terminal MUST be latched from the relayed event frames rather than read from the last frame: a typed event the source emits after its terminal MUST NOT reset the classification, and a relayed failure terminal MUST stay the outcome whatever is relayed after it, in the completed and the cancelled branch alike. A settlement that fails MUST fall back to a release and record the attempt as `error` with error code `usage_settlement_failed`.

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

#### Scenario: Client cancels after delivered deltas without an output item

- **GIVEN** an API key with a token limit streaming from a model source that emits `response.output_text.delta` frames without `response.output_item.added`
- **WHEN** the client disconnects after a delta was relayed and before the terminal frame
- **THEN** the reservation is finalized at the estimate with cause `client_cancel`
- **AND** the request-log row records status `cancelled`

#### Scenario: Client cancels before any content was delivered

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the client disconnects after `response.created` and before any output item
- **THEN** the reservation is released
- **AND** the request-log row records status `cancelled`

#### Scenario: Client cancels after a vendor event the public contract dropped

- **GIVEN** an API key with a token limit streaming from a model source that emits `response.created`, an event outside `response.*` (for example `codex.rate_limits`) and `response.in_progress`, then pauses
- **WHEN** the client disconnects after `response.in_progress` was relayed
- **THEN** the client received no content-bearing frame and the reservation is released
- **AND** the request-log row records status `cancelled`

#### Scenario: Client cancels after bare-CR framed bookkeeping

- **GIVEN** an API key with a token limit streaming from a model source that frames its events with bare CR line endings (legal SSE) and emits `response.created` and `response.in_progress`, then pauses
- **WHEN** the client disconnects after `response.in_progress` was relayed
- **THEN** both frames were classified as bookkeeping and the reservation is released
- **AND** the request-log row records status `cancelled`

#### Scenario: Client cancels while content is parked ahead of response.created

- **GIVEN** an API key with a token limit streaming from a model source that emits `response.output_text.delta` before any `response.created`
- **WHEN** the client disconnects while that delta is still parked in the public wrapper's pre-created buffer
- **THEN** no event frame reached the client and the reservation is released
- **AND** the request-log row records status `cancelled`

#### Scenario: Client leaves while the first output item is withheld ahead of the pin hook

- **GIVEN** a pre-content hook is armed and the frame parser has observed `response.output_item.added` in a chunk not yet flushed
- **WHEN** the client disconnects while the hook is still in flight
- **THEN** no byte reached the client, the holder records no delivered content and a limited key's reservation is released

#### Scenario: Client leaves after the relayed success terminal

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the client disconnects right after `response.completed` was relayed while the source connection is still open
- **THEN** the request-log row records status `success`
- **AND** the reservation is finalized from the terminal's `usage`

#### Scenario: Failure terminal is never charged

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the source emits `response.created` and then `response.failed` without usage
- **THEN** the client receives the relayed failure and the reservation is released
- **AND** the request-log row records status `error` with error code `model_source_response_failed`

#### Scenario: Typeless error record is never charged

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the source emits `response.created`, an output item and a data record `{"error": {...}}` without a `type` field, and the client (an SDK client running to the wrapper's `response.failed`, or a native Codex client tearing the stream down on the relayed record) stops there
- **THEN** the reservation is released
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

#### Scenario: Typed event trailing a rewritten terminal is never charged

- **GIVEN** an API key with a token limit streaming from a model source
- **WHEN** the source emits `response.created`, `response.output_item.added`, a `response.completed` whose `response` is not an object, and then keeps emitting a typed `response.output_text.delta`
- **THEN** the client receives the public wrapper's `response.failed` with code `invalid_json` before the trailing delta
- **AND** the reservation is released and the request-log row records status `error` with error code `model_source_response_invalid`

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
