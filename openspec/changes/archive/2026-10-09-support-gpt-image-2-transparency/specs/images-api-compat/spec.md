## MODIFIED Requirements

### Requirement: OpenAI-compatible image generation endpoint

The system SHALL expose `POST /v1/images/generations` and accept the OpenAI Images API request shape (`model`, `prompt`, `n`, `size`, `quality`, `background`, `output_format`, `output_compression`, `moderation`, `partial_images`, `stream`, `user`). The endpoint MUST require `model` to start with `gpt-image-` and MUST treat `gpt-image-2` as the default if unspecified; the default is the fixed constant `DEFAULT_PUBLIC_IMAGE_MODEL` in `app/core/openai/images.py` and MUST NOT be operator-configurable. The endpoint MUST NOT expose the internal "host" Responses model used to invoke the built-in `image_generation` tool.

#### Scenario: Compatible image generation request returns a JSON envelope

- **WHEN** a client sends `POST /v1/images/generations` with `model=gpt-image-2`, a non-empty `prompt`, and no `stream`
- **THEN** the service returns 200 with a JSON body of shape `{created, data: [{b64_json, revised_prompt}], usage}` containing exactly one entry

#### Scenario: Unsupported model is rejected

- **WHEN** a client sends `POST /v1/images/generations` with `model` not starting with `gpt-image-`
- **THEN** the service returns 400 with OpenAI `invalid_request_error` and `param: model`

#### Scenario: Per-model parameter rules are enforced for gpt-image-2

- **WHEN** a client sends `gpt-image-2` with `input_fidelity=low|high`, or with `size` violating the gpt-image-2 size constraints (max edge ≤ 3840 px, both edges multiples of 16, ratio ≤ 3:1, total pixels in [655_360, 8_294_400])
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
- **THEN** the service uses `gpt-image-2` as the publicly-effective model for validation, request log accounting, and the internal `image_generation` tool config
- **AND** a `CODEX_LB_IMAGES_DEFAULT_MODEL` value in the environment does not change that default (startup logs the removed-setting warning once)

## ADDED Requirements

### Requirement: Transparent image output preserves settings and bytes

The system SHALL accept GPT Image 2 transparent PNG/WebP generation and editing on public and Codex-native routes. It MUST forward requested image settings and preserve returned bytes without model substitution or synthetic transparency. Transparent JPEG MUST return HTTP 400 `invalid_request_error` with `param=output_format` before upstream calls. Existing per-model validations and upstream errors MUST remain enforced.

#### Scenario: Transparent PNG or WebP reaches upstream

- **WHEN** a client requests GPT Image 2 transparent PNG or WebP generation or editing
- **THEN** the requested model, background, output format, quality, and size are forwarded unchanged
- **AND** upstream image bytes are returned unchanged in JSON or streaming responses

#### Scenario: Transparent JPEG is rejected before upstream

- **WHEN** an Images request specifies `background=transparent` and `output_format=jpeg`
- **THEN** the service returns HTTP 400 with OpenAI `invalid_request_error` and `param=output_format`
- **AND** no upstream request is opened

#### Scenario: Upstream transparency failure remains visible

- **WHEN** upstream rejects a valid transparent GPT Image 2 request
- **THEN** the existing status and error envelope are returned without substituting a model or stripping transparency settings
