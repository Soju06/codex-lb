# images-api-compat Specification

## Purpose
Define the OpenAI-compatible Images API adapter that exposes public `gpt-image-*`
requests while routing through the existing Responses `image_generation` tool
pipeline.
## Requirements
### Requirement: OpenAI-compatible image generation endpoint

The system SHALL expose `POST /v1/images/generations` and accept the OpenAI Images API request shape (`model`, `prompt`, `n`, `size`, `quality`, `background`, `output_format`, `output_compression`, `moderation`, `partial_images`, `stream`, `user`). The endpoint MUST require `model` to start with `gpt-image-` and MUST treat `gpt-image-2` as the default if unspecified. The endpoint MUST NOT expose the internal "host" Responses model used to invoke the built-in `image_generation` tool.

#### Scenario: Compatible image generation request returns a JSON envelope

- **WHEN** a client sends `POST /v1/images/generations` with `model=gpt-image-2`, a non-empty `prompt`, and no `stream`
- **THEN** the service returns 200 with a JSON body of shape `{created, data: [{b64_json, revised_prompt}], usage}` containing exactly one entry

#### Scenario: Unsupported model is rejected

- **WHEN** a client sends `POST /v1/images/generations` with `model` not starting with `gpt-image-`
- **THEN** the service returns 400 with OpenAI `invalid_request_error` and `param: model`

#### Scenario: Per-model parameter rules are enforced for gpt-image-2

- **WHEN** a client sends `gpt-image-2` with `background=transparent` or `input_fidelity=low|high`, or with `size` violating the gpt-image-2 size constraints (max edge ≤ 3840 px, both edges multiples of 16, ratio ≤ 3:1, total pixels in [655_360, 8_294_400])
- **THEN** the service returns 400 with OpenAI `invalid_request_error` describing the rejected parameter

#### Scenario: Per-model parameter rules are enforced for legacy gpt-image models

- **WHEN** a client sends `gpt-image-1.5`, `gpt-image-1`, or `gpt-image-1-mini` with `size` outside `{1024x1024, 1536x1024, 1024x1536, auto}`
- **THEN** the service returns 400 with OpenAI `invalid_request_error` and `param: size`

#### Scenario: Multi-image requests are rejected until upstream support arrives

- **WHEN** a client sends `/v1/images/generations` or `/v1/images/edits` with `n > 1`
- **THEN** the service returns 400 with OpenAI `invalid_request_error` and `param: n`, with a message that explains the upstream `image_generation` tool does not yet support multi-image responses
- **AND** no settings override SHALL raise the accepted request `n` above 1 until codex-lb implements client-side fan-out or upstream exposes first-class multi-image support

#### Scenario: Missing model defaults to images_default_model

- **WHEN** a client sends `/v1/images/generations` or `/v1/images/edits` without `model`
- **THEN** the service uses `images_default_model` (default `gpt-image-2`) as the publicly-effective model for validation, request log accounting, and the internal `image_generation` tool config

### Requirement: OpenAI-compatible image edit endpoint

The system SHALL expose `POST /v1/images/edits` and accept the OpenAI Images Edits multipart shape (`image` repeatable file part, optional `mask`, plus `model`, `prompt`, `n`, `size`, `quality`, `background`, `output_format`, `output_compression`, `partial_images`, `stream`, `user`). The endpoint MUST apply the same model gating and parameter rules as `/v1/images/generations`. The endpoint MUST forward `image[]` and `mask` parts as `input_image` content (base64 data URLs) inside the internal Responses request.

#### Scenario: Compatible image edit request returns a JSON envelope

- **WHEN** a client sends multipart `POST /v1/images/edits` with at least one `image` file part, `model=gpt-image-2`, and a non-empty `prompt`
- **THEN** the service returns 200 with a JSON body of shape `{created, data: [{b64_json, revised_prompt}], usage}`

#### Scenario: Unsupported variations endpoint is rejected

- **WHEN** a client sends `POST /v1/images/variations`
- **THEN** the service returns 404 with OpenAI `not_found_error` and a message indicating that variations are not supported

### Requirement: Image generation is implemented as a Responses tool adapter

The system SHALL implement `/v1/images/generations` and `/v1/images/edits` by issuing an internal `/v1/responses` request whose `tools` array includes `{"type": "image_generation", ...}` and whose `input` is constructed to deterministically force a single `image_generation` tool call. The system MUST route that internal request through the existing proxy account-selection, sticky session, retry, and authentication pipeline. The system MUST NOT introduce a new `chatgpt-token → openai-api-key` token-exchange path solely to support these endpoints.

#### Scenario: Internal Responses call uses existing routing

- **WHEN** any `/v1/images/*` request is processed
- **THEN** account selection, sticky-session affinity, API-key validation, and request budgeting use the same code paths as `/v1/responses`

#### Scenario: Multipart edits become input_image content

- **WHEN** an edit request includes `image` and optional `mask` multipart parts
- **THEN** each binary part is encoded as a `data:` URL and inserted as `input_image` content in the internal Responses input

### Requirement: Image generation streaming uses canonical OpenAI Images events

When a client requests `stream=true` on `/v1/images/generations` or `/v1/images/edits`, the system SHALL translate upstream Responses SSE events into the OpenAI Images streaming format. The system MUST emit `image_generation.partial_image` for each upstream `response.image_generation_call.partial_image` and an `image_generation.completed` event for *every* `image_generation_call` ResponseItem the upstream surfaces, in arrival order, when the trailing `response.completed` arrives. The `usage` field MUST be attached only to the final `image_generation.completed` event so multi-image responses match the OpenAI Images streaming shape. The system MUST NOT forward Responses-specific events (`response.created`, `response.in_progress`, `response.image_generation_call.in_progress`, `response.image_generation_call.generating`, reasoning/content events) to the client. The system MUST also surface upstream errors that occur before the first SSE chunk as a structured OpenAI error envelope rather than a broken/truncated stream body.

#### Scenario: Partial images are forwarded with stable field names

- **WHEN** the upstream stream emits `response.image_generation_call.partial_image` with `partial_image_b64` and `partial_image_index`
- **THEN** the client receives `image_generation.partial_image` with `b64_json` set to `partial_image_b64`, the same `partial_image_index`, and the upstream `size`, `quality`, `background`, and `output_format`

#### Scenario: Final image completes the stream

- **WHEN** the upstream stream emits `response.output_item.done` with `item.type == "image_generation_call"` and a non-empty `result`
- **THEN** the client receives `image_generation.completed` with `b64_json`, `revised_prompt`, `size`, `quality`, `background`, and `output_format`, followed by a terminating `[DONE]`-equivalent event

#### Scenario: Upstream image generation failure becomes a single error event

- **WHEN** the upstream stream surfaces `response.failed` or an `image_generation_call` with `status == "failed"`
- **THEN** the client receives a single `error` event using an OpenAI error envelope and the SSE stream is closed cleanly

### Requirement: Image routes participate in usage accounting and policy

The system SHALL apply API-key allowed-model policy and model-scoped usage
limits to `/v1/images/*` using the publicly-requested `gpt-image-*` value as the
effective model. The system SHALL record the publicly-requested `gpt-image-*`
value (not the internal host model) in the request log's `model` column once the
upstream response id becomes known. A successful image generation or edit that
owns a limited API-key reservation SHALL transfer that reservation exactly once
to persistence-drained settlement using captured `tool_usage.image_gen` tokens,
while the internal Responses stream SHALL NOT receive a second settlement
owner. Failed or cancelled finalization SHALL preserve the completed public
image response and transfer ownership to the tracked retrying release fallback.

#### Scenario: API key allowed-model policy blocks gpt-image-2

- **WHEN** an API key's `allowed_models` list does not include `gpt-image-2`
- **THEN** requests to `/v1/images/generations` or `/v1/images/edits` with `model=gpt-image-2` return 403 `model_not_allowed`

#### Scenario: Request log surfaces the publicly requested image model

- **WHEN** an `/v1/images/*` request completes successfully against an internal host Responses model (for example `gpt-5.5`)
- **THEN** the resulting `request_logs` row has `model` equal to the publicly requested value (for example `gpt-image-2`) so dashboards and usage views surface the user-visible model rather than the internal host model

#### Scenario: Failed image-token settlement retains tracked release ownership

- **GIVEN** a limited API key owns a reservation for a successful image generation or edit request
- **AND** the internal Responses stream receives no API-key reservation
- **AND** the image adapter captures authoritative `tool_usage.image_gen` tokens
- **WHEN** tracked finalization fails or is cancelled while the reservation remains `reserved`
- **THEN** the completed public Images JSON response or SSE completion remains available
- **AND** settlement ownership transfers to a persistence-drained fallback release task
- **AND** transient release failures keep that task tracked and retrying until release succeeds or graceful persistence drain reports timeout
- **AND** a successful fallback restores pre-reserved quota exactly once without recording `response.usage` or starting a second image settlement

### Requirement: Image routes expose bounded operational observability

The system SHALL emit structured route-completion logs and Prometheus metrics for `/v1/images/generations` and `/v1/images/edits`. Observability labels MUST be bounded to route, effective public model, stream flag, HTTP status, and outcome, and MUST NOT include prompts, image bytes, file names, access tokens, or raw upstream payloads.

#### Scenario: Successful image request records completion telemetry

- **WHEN** an `/v1/images/generations` or `/v1/images/edits` request completes successfully
- **THEN** the service emits an `images_route_complete` log line with the public image route, public model, stream flag, status, outcome, and duration
- **AND** increments `codex_lb_image_requests_total` and observes `codex_lb_image_request_duration_seconds` with the same bounded labels

#### Scenario: Failed image request records completion telemetry

- **WHEN** an image request is rejected by validation or mapped from an upstream/image-generation error
- **THEN** the service emits the same bounded `images_route_complete` fields with a non-success outcome
- **AND** increments the image request counter and duration histogram without logging prompt or binary image content

### Requirement: Codex-base Images API aliases

The system SHALL expose `POST /backend-api/codex/images/generations` and
`POST /backend-api/codex/images/edits` as Codex-base equivalents of the
existing `/v1/images/generations` and `/v1/images/edits` handlers. The edit
route MUST accept Codex's JSON `images` array, whose entries contain base64
`image_url` data URLs, and decode those entries before it delegates to the
existing edit pipeline. The aliases MUST apply the same authentication,
validation, account-routing, observability, response-shape, and error-envelope
behavior as their `/v1` counterparts. The aliases MUST NOT be included in the
OpenAPI schema because `/v1/images/*` remains the canonical OpenAI-compatible
surface.

#### Scenario: Codex-base image generation uses the existing handler

- **WHEN** a Codex client sends `POST /backend-api/codex/images/generations`
  with an invalid image model
- **THEN** the service returns the same 400 OpenAI `invalid_request_error` with
  `param: model` as `POST /v1/images/generations`

#### Scenario: Codex-base image editing uses the existing handler

- **WHEN** a Codex client sends JSON `POST /backend-api/codex/images/edits`
  without a non-empty `images[].image_url` data URL
- **THEN** the service returns a 400 OpenAI `invalid_request_error` with
  `param: images`, rather than `405 Method Not Allowed` or a missing-prompt error

#### Scenario: Codex-base alias failures before the handler record route observability

- **WHEN** a request to `POST /backend-api/codex/images/generations` or
  `POST /backend-api/codex/images/edits` fails before the route handler runs
  (for example API-key authentication or request-body validation handled by
  the shared exception layer)
- **THEN** the service records the `images_route_complete` observability entry
  (log and metrics) with the same `generations`/`edits` route label as the
  `/v1` counterpart, exactly once

### Requirement: Image edit multipart uploads are authorized and bounded

`POST /v1/images/edits` MUST complete its existing proxy authorization dependencies before reading multipart body bytes. It MUST accept at most 16 source-image file parts across `image` and `image[]`, at most one `mask`, no unknown file-part names, no more than 32 text fields of at most 256 KiB each, every individual file smaller than 50,000,000 bytes, fewer than 50,000,000 bytes across all source images and the mask, and a complete multipart body no greater than 64 MiB (67,108,864 bytes).

The service MUST enforce the body limit against both a usable declared `Content-Length` and actual streamed bytes. It MUST enforce file, aggregate-binary, and text limits before retaining crossing bytes, close multipart spools before usage reservation, account selection, base64 conversion, or internal Responses forwarding, and add no new runtime setting.

This route-owned policy MUST take precedence over the generic raw HTTP body budget for `POST /v1/images/edits`. Its exact-path content-encoding gate MUST run outside the generic raw and decompression guards regardless of the declared media type. Requests handled by that gate, and unencoded requests declared as multipart, MUST NOT be rejected by the generic guards before proxy authorization or the dedicated parser applies this capability's body limit. An unencoded request that does not declare multipart remains under generic admission and MAY be rejected there before authorization. This exception MUST NOT change generic ingress behavior for any other operation.

Byte-limit failures MUST return HTTP 413 with OpenAI error `code = payload_too_large` and `type = invalid_request_error`; a known file-part failure MUST set `param = image` or `param = mask`. Multipart syntax, count, and required-field failures MUST retain OpenAI-compatible invalid-request behavior. Every parser rejection MUST emit exactly one bounded image-route observation with HTTP status and `outcome = invalid_request`.

#### Scenario: Unauthorized image edit does not consume the body

- **WHEN** an image-edit request fails the existing proxy API-key authorization
- **THEN** the authentication response is returned before the ASGI request body is consumed
- **AND** no multipart temporary file is created
- **AND** exactly one auth-error route observation is recorded without parsing the multipart body, using bounded pre-parse labels

#### Scenario: Bounded image edit remains compatible

- **WHEN** an authorized image-edit request supplies at least one source image, an optional mask, required text fields, and all parts are within their limits
- **THEN** the service preserves the ordered `image` and `image[]` bytes, content types, mask, and validated form fields through the existing image-edit pipeline

#### Scenario: Source image count combines canonical and bracketed keys

- **WHEN** the combined number of `image` and `image[]` file parts exceeds 16, the request contains more than one `mask`, or an unknown file-part name is present
- **THEN** the service returns an OpenAI-compatible HTTP 400 invalid-request response
- **AND** no image bytes are base64-encoded or forwarded internally

#### Scenario: Declared or streamed image-edit body exceeds its limit

- **WHEN** a usable `Content-Length` exceeds 64 MiB or actual streamed multipart bytes cross 64 MiB
- **THEN** the service returns HTTP 413 with OpenAI error `code = payload_too_large` and `type = invalid_request_error`
- **AND** no usage reservation, account selection, base64 conversion, or internal Responses request occurs

#### Scenario: Image binary limit is exceeded

- **WHEN** one source image or mask reaches 50,000,000 bytes, or their combined binary bytes reach 50,000,000
- **THEN** the service returns HTTP 413 with OpenAI error `code = payload_too_large`, `type = invalid_request_error`, and the applicable `image` or `mask` parameter
- **AND** bytes beyond the applicable limit are not retained in a spool or handler buffer

#### Scenario: Image text-field resources are bounded

- **WHEN** an image-edit request exceeds 32 text fields or 256 KiB in any text part
- **THEN** the service rejects the request with the documented OpenAI-compatible count or byte-limit response
- **AND** it records one invalid-request route observation without invoking image-edit route logic

#### Scenario: Compressed image edit is rejected without prebuffering

- **GIVEN** image edit has passed proxy authorization
- **WHEN** it declares a non-identity `Content-Encoding`
- **THEN** the service returns HTTP 400 with OpenAI error `code = invalid_request_error` and `type = invalid_request_error` before reading the request body
- **AND** a no-op `identity` encoding is handled as an ordinary multipart request governed by the 64 MiB dedicated body limit
- **AND** exactly one invalid-request route observation is recorded without parsing the multipart body

#### Scenario: Generic ingress does not preempt encoded image-edit authorization

- **GIVEN** an image-edit request fails proxy authorization
- **WHEN** it declares a non-identity `Content-Encoding` and a `Content-Length` greater than the generic raw HTTP budget
- **THEN** the existing authentication response is returned instead of a generic HTTP 413 or encoded-body HTTP 400
- **AND** the request body is not consumed
- **AND** exactly one auth-error route observation is recorded

#### Scenario: Image-edit cleanup preserves transport failures

- **WHEN** parsing succeeds, fails a limit, encounters malformed multipart, receives a client disconnect, or is cancelled
- **THEN** every created multipart spool is closed
- **AND** disconnect and cancellation are not converted to HTTP 413

