## ADDED Requirements

### Requirement: OpenAI-compatible model-source transport is bounded and isolated

Every request the proxy forwards to an OpenAI-compatible model source MUST use a dedicated outbound connector pool that is separate from the ChatGPT upstream connector. The pool MUST be sized by `http_connector_limit` / `http_connector_limit_per_host`, MUST tunnel through the same SOCKS proxy configuration as the ChatGPT pool, and MUST be built and retired with the shared client generation: rotation defers closing the retired pool until every in-flight source exchange has released its lease. A source that accepts connections and stalls MUST NOT consume ChatGPT connector slots.

Source exchanges MUST bound their exposure per phase. TCP connection establishment MUST be bounded by 10 seconds via `sock_connect`; a connect timeout keeps the existing `502 model_source_unreachable` verdict and records the `connect` phase. The 10-second establishment bound MUST NOT also bound the wait for a free pooled connection: aiohttp's `connect` timeout bounds that pool wait as well, so it MUST NOT be armed. A source whose dedicated pool is saturated at `http_connector_limit_per_host` MUST queue for a free connection under the source's total budget (and, for a streaming Responses open, the header deadline) rather than fail fast at the establishment deadline as `model_source_unreachable`, so a source with `max_concurrency` unset ("unlimited") is not silently capped at the connector's per-host limit and a busy source does not feed false breaker trips. For streaming Responses requests the wait for response headers MUST be bounded by 20 seconds and the wait for the first body chunk after the headers by 30 seconds; either expiry MUST fail the request with HTTP `504` and error code `model_source_timeout` before any byte of a `200` stream reaches the client, and MUST release the source connection. These two bounds are justified by the Responses shape alone — a compliant source emits `response.created` within milliseconds of its headers — and MUST NOT be armed for chat-completions streams, whose first byte is the first token and whose OpenAI-compatible local servers send their headers and first token only after prompt processing: a chat-completions stream open MUST return once the source's response headers are read, exactly as before the hardening, so the client receives the `200` and headers immediately and a client that leaves during prompt processing cancels the stream body and releases the source connection, the pooled lease and its API-key reservation without waiting for the first token or the source's total budget; the first chunk is read inside the stream body under the source's total budget alone (a total-budget or transport failure there keeps the `502 model_source_unreachable` verdict it had before the hardening), and the idle bound is armed only after that first chunk arrived. Every chat-completions stream MUST own its source transport the way a Responses stream does: the streaming response's transport finalizer MUST run when the response exits, and a client that leaves after the route returned the response but before the first body write completes -- the one await in which the body is never iterated, so the body's own `finally` never runs -- MUST close the source connection, release the pooled lease and the API-key reservation, and record the request as `cancelled` with error code `client_disconnected_before_body`; no part of a source exchange MAY be left to garbage collection. A chat-completions `2xx` stream that ends before its first chunk MUST NOT raise out of the started body: the client already holds the `200` and headers, so the body ends as the clean empty stream it received before the hardening, while the stream owner records the attempt as `error invalid_upstream_response`; the limited-key chat stream, which buffers the source stream and has sent no byte, MUST answer `502 invalid_upstream_response` and release the reservation. Mid-stream silence MUST be bounded by the smaller of `stream_idle_timeout_seconds` and 300 seconds — a source never inherits the subscription idle window — and an expiry MUST surface to the stream owner as `504 model_source_idle_timeout`. The idle bound MUST start only after the first frame has been read and MUST NOT shorten the header or first-frame deadlines, whatever idle window the operator configured; the source's total budget expiring mid-stream is not reported as idleness. A Responses `2xx` stream that ends before its first chunk (read by the open, before any client byte) MUST fail with `502 invalid_upstream_response`. Non-stream forwards MUST bound only connection establishment and the source's total budget (`timeout_seconds`, default 600 s), so a long generation that sends nothing until its final body is not cut by a header or first-frame deadline. The stream deadlines MUST be armed through the owner scheduler seam so simulated time can expire them.

Source `4xx`/`5xx` answers MUST pass through honestly: the source status code, the source error envelope with the proxy's source credential redacted, and the source `Retry-After` value MUST be preserved for the caller, and the proxy MUST NOT synthesize a `Retry-After`. Every source route — Responses dispatch, chat completions (stream and non-stream), embeddings and audio transcription — MUST relay that `Retry-After` on the error response it answers the client with. For Responses dispatch a source `401` or `403` MUST instead be answered with `502 model_source_credentials_error` and a fixed generic message; the source body MUST NOT be read, forwarded, or logged, and the server-side record MUST carry only the source id and the status code. Chat-completions, transcription, and embeddings source routes keep passing the source's `401`/`403` envelope through.

For Responses streams the stream body MUST yield the first chunk the open already read before reading further, so the first-frame deadline is the only wait between the headers and the first byte the client receives.

While a pre-content hook is armed on a Responses stream, the frames withheld ahead of the hook MUST be classified by the same frame parser that classifies complete frames, at EOF included. The frame parser MUST read a frame's event type exactly as the public wrapper's classifier does: a data record without a `type` field that carries an `error` object is the `error` failure terminal (flushed without the hook, never withheld as bookkeeping until EOF), and a typeless record without one is untyped bookkeeping. At EOF: a final record the source closed without the blank-line terminator MUST be parsed as a frame before the withheld bytes are released, so a success terminal or content frame in that tail runs the hook first and a failure terminal or an empty tail flushes without it. The withheld bytes MUST be bounded by an aggregate cap of twice the parser's single-frame cap; a source that keeps producing complete bookkeeping frames past that bound MUST fail closed with `502 invalid_upstream_response` and release the source connection, never flush unpinned and never run the hook on bookkeeping alone. Without a hook nothing is withheld and the cap does not apply. The frame parser MUST ignore one leading UTF-8 byte-order mark exactly as the proxy's event-block reassembler does, so a source that prefixes its stream with a BOM never has its first record delivered by the wrapper while the parser observes nothing. The frame parser MUST treat a CRLF line ending whose CR closes one transport chunk and whose LF opens the next as a single line ending, exactly as the reassembler does: the split MUST NOT become a frame boundary, so a frame cut that way is still one frame to the parser (its usage, terminal kind and content classification reach the holder and the pre-content hook runs before it is released).

#### Scenario: source accepts the TCP connection but never sends headers

- **WHEN** a streaming Responses request is forwarded to a source that accepts the connection and sends nothing for 20 seconds
- **THEN** the request fails with HTTP `504` and `error.code` `model_source_timeout`
- **AND** the source connection is released
- **AND** no byte of a `200` stream was sent to the client

#### Scenario: source sends headers and then no frame

- **WHEN** a streaming source answers `200 text/event-stream` and sends no body byte for 30 seconds
- **THEN** the request fails with HTTP `504` and `error.code` `model_source_timeout`
- **AND** the source connection is closed rather than returned to the pool

#### Scenario: chat-completions stream survives slow prompt processing

- **WHEN** a streaming chat-completions request is forwarded to a source that sends its headers after 25 seconds and its first token 35 seconds later
- **THEN** the open completes when the headers arrive and the stream is delivered with its usage and no `model_source_timeout` is raised
- **AND** the same silences on a streaming Responses request expire the header and first-frame deadlines

#### Scenario: client abandons a chat-completions stream during prompt processing

- **GIVEN** a streaming chat-completions request forwarded to a source that has sent its `200` headers and is still processing the prompt
- **WHEN** the client disconnects before the first token
- **THEN** the client had already received the `200` and headers, the request handler returns promptly, the source connection is closed and the pooled lease released
- **AND** the request is recorded as `cancelled` with error code `client_disconnected`

#### Scenario: client abandons a chat-completions stream before the body starts

- **GIVEN** a streaming chat-completions request whose source has sent its `200` headers, so the route has returned the streaming response
- **WHEN** the client is gone before the proxy's first write (`http.response.start`) completes, so the response body is never iterated
- **THEN** the source connection is closed, the pooled lease is released and no source connection stays acquired
- **AND** the API-key reservation, if any, is released
- **AND** the request is recorded as `cancelled` with error code `client_disconnected_before_body`

#### Scenario: stream idle cap never inherits the subscription window

- **WHEN** `stream_idle_timeout_seconds` is 7200 and a source stream goes silent after `response.created`
- **THEN** the stream fails with `504 model_source_idle_timeout` 300 seconds after the last frame, never after 7200 seconds
- **AND** the source connection is released

#### Scenario: a low idle window leaves the open deadlines intact

- **WHEN** `stream_idle_timeout_seconds` is 5 and a source sends its headers after 15 seconds and its first frame 25 seconds later
- **THEN** the open succeeds under the 20-second header and 30-second first-frame deadlines
- **AND** only silence after the first frame is bounded by 5 seconds

#### Scenario: non-stream generation outlives the stream deadlines

- **WHEN** a non-stream Responses request is forwarded to a source whose complete body arrives after 20 minutes within the source's total budget
- **THEN** the response is delivered
- **AND** neither the header nor the first-frame deadline is armed

#### Scenario: a saturated source pool queues rather than reporting unreachable

- **GIVEN** a source whose dedicated pool holds one connection per host and one streaming Responses request already in flight
- **WHEN** a second streaming Responses request is forwarded to the same source before the first releases its connection
- **THEN** the second open waits for a free connection past the 10-second establishment deadline instead of failing with `502 model_source_unreachable`
- **AND** it proceeds once the first request releases its connection

#### Scenario: stalled sources do not consume the ChatGPT connector

- **WHEN** fifty streaming source opens stall waiting for headers
- **THEN** the ChatGPT connector has no acquired connections attributable to them
- **AND** the model-source connector holds those fifty connections until the header deadline releases them

#### Scenario: unterminated success terminal at EOF runs the hook first

- **GIVEN** a pre-content hook is armed and the source has produced only `response.created`
- **WHEN** the source writes `data: {"type":"response.completed", ... "output":[...]}` followed by a single newline and closes the connection
- **THEN** the hook runs once with terminal kind `completed` before any withheld byte is released
- **AND** the client receives `response.created` and the completed frame in order

#### Scenario: typeless error record is the failure terminal

- **GIVEN** a pre-content hook is armed and the source has produced only `response.created`
- **WHEN** the source writes `data: {"error":{"message":"overloaded", ...}}` without a `type` field and keeps the connection open
- **THEN** the withheld frames flush at once, the hook never runs and the holder records terminal kind `error`

#### Scenario: unterminated failure terminal at EOF flushes without the hook

- **GIVEN** a pre-content hook is armed and the source has produced only `response.created`
- **WHEN** the source closes after an unterminated `response.failed` record
- **THEN** the withheld frames are flushed, the hook never runs and the holder records terminal kind `failed`

#### Scenario: BOM-prefixed data-only terminal runs the hook first

- **GIVEN** a pre-content hook is armed
- **WHEN** the source's first bytes are a UTF-8 byte-order mark followed by a data-only `response.completed` frame carrying `output` and `usage`
- **THEN** the hook runs once with terminal kind `completed` before the frame is released, the frame is relayed byte-identically and the holder records the frame's usage

#### Scenario: CRLF split across transport chunks is one line ending

- **GIVEN** a pre-content hook is armed and the source frames its stream with CRLF line endings
- **WHEN** a `response.completed` frame arrives as two chunks, the first ending with the CR of a line ending inside the frame and the second starting with its LF
- **THEN** the parser observes one frame with terminal kind `completed` and its usage
- **AND** the hook runs once before any byte of the frame is released and the frame is relayed byte-identically

#### Scenario: bookkeeping past the withheld cap fails closed

- **GIVEN** a pre-content hook is armed
- **WHEN** the source produces complete `response.in_progress` frames whose total exceeds the withheld-bytes cap without a content frame
- **THEN** the stream fails with `502 invalid_upstream_response`, nothing was delivered, the hook never ran and the source connection is released
- **AND** the same frames stream live when no hook is armed

#### Scenario: chat-completions source closes before its first chunk

- **GIVEN** a chat-completions stream whose open returned at the source's `200` headers
- **WHEN** the source closes the body without a single chunk
- **THEN** the stream body ends cleanly with no chunk and no exception, the source connection is released and `first_frame_at` was never stamped
- **AND** an unlimited-key client receives the empty `200` stream while the request-log row records `error invalid_upstream_response`
- **AND** a limited-key client receives `502 invalid_upstream_response` before any byte and its reservation is released

#### Scenario: source 429 passes through with Retry-After

- **WHEN** a source answers `429` with `Retry-After: 7` and an error envelope
- **THEN** the forwarding error carries status `429`, the source envelope, and `retry_after` `"7"`

#### Scenario: every source route relays the source Retry-After

- **WHEN** a source answers a chat-completions (stream or non-stream), embeddings or audio-transcription request with `429`, `Retry-After: 7` and an error envelope
- **THEN** the client receives HTTP `429` with the source's envelope and `Retry-After: 7`

#### Scenario: source 401 on Responses dispatch is recoded without leaking the key

- **WHEN** a source answers a Responses request with `401` and a body that embeds a masked copy of the proxy's source credential
- **THEN** the request fails with HTTP `502` and `error.code` `model_source_credentials_error`
- **AND** no fragment of the source body appears in the client envelope or in any log record
- **AND** a `WARNING` record names the source id and the status `401` only

#### Scenario: chat completions keep the source 401 envelope

- **WHEN** a source answers a streaming chat-completions request with `401 invalid_api_key`
- **THEN** the client receives HTTP `401` with `error.code` `invalid_api_key`
