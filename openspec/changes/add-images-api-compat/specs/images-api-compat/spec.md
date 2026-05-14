## ADDED Requirements

### Requirement: OpenAI-compatible image generation endpoint

The system SHALL expose `POST /v1/images/generations` and accept the OpenAI Images API request shape (`model`, `prompt`, `n`, `size`, `quality`, `background`, `output_format`, `output_compression`, `moderation`, `partial_images`, `stream`, `user`). The endpoint MUST require `model` to start with `gpt-image-` and MUST treat `gpt-image-2` as the default if unspecified. The endpoint MUST NOT expose the internal "host" Responses model used to invoke the built-in `image_generation` tool.

#### Scenario: Compatible image generation request returns a JSON envelope

- **WHEN** a client sends `POST /v1/images/generations` with `model=gpt-image-2`, a non-empty `prompt`, and no `stream`
- **THEN** the service returns 200 with a JSON body of shape `{created, data: [{b64_json, revised_prompt}], usage}` containing exactly `n` (or 1 by default) entries

#### Scenario: Unsupported model is rejected

- **WHEN** a client sends `POST /v1/images/generations` with `model` not starting with `gpt-image-`
- **THEN** the service returns 400 with OpenAI `invalid_request_error` and `param: model`

#### Scenario: Per-model parameter rules are enforced for gpt-image-2

- **WHEN** a client sends `gpt-image-2` with `background=transparent` or `input_fidelity=low|high`, or with `size` violating the gpt-image-2 size constraints (max edge ≤ 3840 px, both edges multiples of 16, ratio ≤ 3:1, total pixels in [655_360, 8_294_400])
- **THEN** the service returns 400 with OpenAI `invalid_request_error` describing the rejected parameter

#### Scenario: Per-model parameter rules are enforced for legacy gpt-image models

- **WHEN** a client sends `gpt-image-1.5`, `gpt-image-1`, or `gpt-image-1-mini` with `size` outside `{1024x1024, 1536x1024, 1024x1536, auto}`
- **THEN** the service returns 400 with OpenAI `invalid_request_error` and `param: size`

#### Scenario: Non-streaming multi-image generation fans out internally

- **WHEN** a client sends non-streaming `POST /v1/images/generations` with `n > 1` and `n <= images_max_n`
- **THEN** the service performs `n` internal Responses requests using the `image_generation` tool
- **AND** returns 200 with a JSON body of shape `{created, data: [{b64_json, revised_prompt}], usage}` containing exactly `n` entries
- **AND** the `data` entries preserve the deterministic fan-out order

#### Scenario: Non-streaming multi-image edits fan out internally

- **WHEN** a client sends non-streaming `POST /v1/images/edits` with `n > 1` and `n <= images_max_n`
- **THEN** the service performs `n` internal Responses requests using the same uploaded image and mask inputs for each fan-out attempt
- **AND** returns 200 with a JSON body of shape `{created, data: [{b64_json, revised_prompt}], usage}` containing exactly `n` entries
- **AND** the `data` entries preserve the deterministic fan-out order

#### Scenario: Multi-image requests above the configured cap are rejected

- **WHEN** a client sends `/v1/images/generations` or `/v1/images/edits` with `n > images_max_n`
- **THEN** the service returns 400 with OpenAI `invalid_request_error` and `param: n`, with a message that references the configured cap

#### Scenario: Streaming multi-image requests remain unsupported until streaming fan-out is specified

- **WHEN** a client sends `/v1/images/generations` or `/v1/images/edits` with `stream=true` and `n > 1`
- **THEN** the service returns 400 with OpenAI `invalid_request_error` and `param: n`, with a message that explains streaming multi-image fan-out is not yet supported

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

The system SHALL implement `/v1/images/generations` and `/v1/images/edits` by issuing internal `/v1/responses` request(s) whose `tools` array includes `{"type": "image_generation", ...}` and whose `input` is constructed to deterministically force a single `image_generation` tool call per internal request. For non-streaming requests with `n > 1`, the system MUST fan out into one internal Responses request per requested image because the upstream `image_generation` tool accepts only one image per call. The system MUST route every internal request through the existing proxy account-selection, sticky session, retry, and authentication pipeline. The system MUST NOT introduce a new `chatgpt-token → openai-api-key` token-exchange path solely to support these endpoints.

#### Scenario: Internal Responses call uses existing routing

- **WHEN** any `/v1/images/*` request is processed
- **THEN** account selection, sticky-session affinity, API-key validation, and request budgeting use the same code paths as `/v1/responses`

#### Scenario: Multipart edits become input_image content

- **WHEN** an edit request includes `image` and optional `mask` multipart parts
- **THEN** each binary part is encoded as a `data:` URL and inserted as `input_image` content in the internal Responses input

#### Scenario: Fan-out failure returns one public error envelope

- **WHEN** any internal Responses request in a non-streaming multi-image fan-out fails before producing a valid image result
- **THEN** the public `/v1/images/*` request fails as a single OpenAI error envelope
- **AND** the service does not return a partial-success image list

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

The system SHALL apply API-key allowed-model policy and model-scoped usage limits to `/v1/images/*` using the publicly-requested `gpt-image-*` value as the effective model. The system SHALL record the publicly-requested `gpt-image-*` value (not the internal host model) in request-log rows once upstream response ids become known. For non-streaming multi-image fan-out, the system SHALL aggregate image usage from all successful internal Responses calls into the single public response's `usage` block and SHALL finalize API-key usage against the same public `gpt-image-*` model.

#### Scenario: API key allowed-model policy blocks gpt-image-2

- **WHEN** an API key's `allowed_models` list does not include `gpt-image-2`
- **THEN** requests to `/v1/images/generations` or `/v1/images/edits` with `model=gpt-image-2` return 403 `model_not_allowed`

#### Scenario: Request log surfaces the publicly requested image model

- **WHEN** an `/v1/images/*` request completes successfully against an internal host Responses model (for example `gpt-5.5`)
- **THEN** every resulting `request_logs` row has `model` equal to the publicly requested value (for example `gpt-image-2`) so dashboards and usage views surface the user-visible model rather than the internal host model

#### Scenario: Fan-out usage is aggregated in the public response

- **WHEN** a non-streaming `/v1/images/*` request fans out into multiple internal Responses calls
- **THEN** the public response `usage` block sums `input_tokens`, `output_tokens`, `total_tokens`, and token-detail fields across all successful internal calls
