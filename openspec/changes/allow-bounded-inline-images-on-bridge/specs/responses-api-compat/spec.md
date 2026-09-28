# responses-api-compat delta

## ADDED Requirements

### Requirement: Bounded inline images ride the HTTP responses bridge

This requirement carves an explicit exception into "Responses input images
bypass the HTTP responses bridge" and "Oversized responses request payloads
fall back to HTTP": a request admitted by this contract keeps using the HTTP
responses bridge despite carrying `input_image` parts, is exempt from the
payload-size bridge bypass and the size-driven upstream-HTTP pin, and is
exempt from historical slimming. Every image-bearing request that is NOT
admitted keeps exactly the pre-existing bypass, pin and reject behavior.

Admission is governed by `http_responses_session_bridge_inline_images_enabled`
(env `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INLINE_IMAGES_ENABLED`), default
`true`. A request is admitted iff the setting is enabled, the request
declares no `image_generation` tool, and EVERY `input_image` part anywhere in
the input (top-level items, nested message content, tool output content, any
deeper nesting) has a string `image_url` of the form
`data:image/jpeg;base64,<segment>` or `data:image/png;base64,<segment>`
(prefix matched case-insensitively) whose non-empty segment is valid strict
base64 decoding to at most 5,000,000 bytes (inclusive). A base64 segment
longer than 6,666,668 characters MUST be rejected as over-budget without
decoding. Setting the flag explicitly to `false` is the documented rollback:
every image-bearing request then takes the blanket image bypass exactly as
before this contract.

A shape-valid request whose any image decodes above 5,000,000 bytes MUST be
rejected with HTTP 400, code `payload_too_large`, `param=input`,
`error_type=invalid_request_error`, before any upstream send; it MUST NOT
take the image bypass, be slimmed, or fall back to raw HTTP for size. An
admitted request whose complete serialized `response.create` frame —
envelope, bridge operation id, thread-cache identity and account installation
metadata included — exceeds 64 MiB (67,108,864 bytes) MUST be rejected with
the same error, measured exactly at the final send, with an earlier
serialized-size estimate MAY reject clear oversize earlier; multiple
individually legal images and long history MUST NOT be silently slimmed,
dropped or rerouted for size. Bridge upstream connections opened while the
contract is enabled MUST use the 64 MiB receive cap on every adapter,
including sockets first opened by a text turn; the explicit rollback and
non-bridge callers keep their stock caps. The two documented fallbacks — an
operator's explicit `upstream_stream_transport = "http"` pin and the
recent-websocket-failure health fallback — keep their existing logged
behavior; no size-only bypass may be introduced for admitted requests.

#### Scenario: Bounded inline image keeps the bridge session

- **GIVEN** the HTTP responses bridge is enabled and no inline-image env override is set
- **WHEN** a thread's turn carries inline `data:image/(png|jpeg);base64,` images each decoding to at most 5,000,000 bytes
- **THEN** the turn is served on the thread's existing bridge connection with the image bytes verbatim upstream
- **AND** `prompt_cache_key` continuity is preserved and no new upstream connection is opened for the image turn

#### Scenario: Per-image boundary is inclusive on decoded bytes

- **WHEN** a request carries one inline image decoding to exactly 5,000,000 bytes
- **THEN** it is admitted subject to the frame budget
- **WHEN** a request carries one inline image decoding to 5,000,001 bytes
- **THEN** the client receives HTTP 400 `payload_too_large` with `param=input` before any upstream send
- **AND** no bridge connection is opened and no account side effect outlives the request

#### Scenario: Multiple legal images and long history fit the frame budget

- **WHEN** a request carries several individually legal inline images plus long history and its complete serialized frame exceeds the stock websocket frame budget but stays at or below 64 MiB
- **THEN** it is locally admitted without slimming or a size-driven raw fallback

#### Scenario: Frame cap covers metadata at the final measurement

- **WHEN** an admitted request passes the early size estimate but metadata stamping pushes its final serialized frame over 64 MiB
- **THEN** the final-send measurement rejects it with 400 `payload_too_large` before the frame is sent upstream

#### Scenario: Explicit false restores the blanket image bypass

- **GIVEN** `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INLINE_IMAGES_ENABLED` is `false`
- **WHEN** any image-bearing request arrives
- **THEN** it bypasses the bridge and records `http_bridge_routing{stage="bypass",reason="image"}` as before
- **AND** the raw path resolves its upstream transport with no image-driven HTTP pin

#### Scenario: Unsupported shapes keep today's behavior and win over size

- **WHEN** a request carries an external `http(s)` image URL, a non-JPEG/PNG media type, malformed base64, a `file_id`/`sediment` reference, or an `image_generation` tool — including mixed with an over-budget inline sibling
- **THEN** the stock bypass or reject behavior applies, with the unsupported shape taking precedence over the per-image budget

#### Scenario: Oversize rejection leaves the lane and account healthy

- **WHEN** one image turn on a bridge-served thread is rejected as over-budget
- **THEN** its pending slot, admission registration and API-key reservation settle
- **AND** the immediately following text turn on the same thread and account proceeds normally

### Requirement: Upstream websocket close 1009 is a terminal payload error

An upstream websocket close carrying close code 1009 (message too big),
observed through any adapter (aiohttp close frame, `websockets` library
close, native helper `websocket_close` event, including a 1009 the local
adapter generates when its own cap rejects an inbound message), MUST be
classified as the terminal non-retryable client error `payload_too_large`
(`invalid_request_error`, `param=input`): HTTP 400 with the OpenAI error
envelope when the downstream response is uncommitted, and the SSE
`response.failed` error envelope when response events have already streamed.
This adds an exact-code exception to the retry/penalty handling in
"Upstream websocket drops penalize affected accounts": a 1009 close MUST NOT
be retried with an identical re-dispatch, MUST NOT replay through the
pre-created retry circuit or transparent websocket replay, MUST NOT exclude
or rotate the account, MUST NOT write account error-health, and MUST NOT be
masked by a `no_accounts` selection failure. Pending slots,
response-create gates, stream lanes and API-key reservations MUST settle
through the ordinary terminal-finalization path. A generic disconnect (no
close frame or close code 1006) and a clean close (1000) keep their existing
semantics.

#### Scenario: Close 1009 before any event is a terminal 400

- **GIVEN** a bridge request has been dispatched on the upstream websocket
- **WHEN** the upstream closes with close code 1009 before any response event
- **THEN** the uncommitted response fails with HTTP 400 `payload_too_large`, `param=input`
- **AND** exactly one upstream dispatch happened, with no exclusion or error-health write, and the account remains selectable for the next request

#### Scenario: Close 1009 after streamed events keeps the SSE contract

- **GIVEN** a bridged stream has already emitted response events downstream
- **WHEN** the upstream closes with close code 1009
- **THEN** the stream terminates with the SSE `response.failed` envelope carrying `payload_too_large`
- **AND** no previously streamed event is duplicated or replayed

#### Scenario: Generic disconnects are not reclassified

- **WHEN** the upstream drops without a close frame or with the synthesized 1006, or closes cleanly with 1000
- **THEN** the existing `stream_incomplete` handling, including its bounded retry semantics, applies unchanged
