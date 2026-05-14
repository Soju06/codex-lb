# Tasks: add-images-api-compat

## 1. Schemas

- [x] 1.1 Add `V1ImagesGenerationsRequest` (model, prompt, n, size, quality, background, output_format, output_compression, moderation, partial_images, stream, user) and `V1ImagesEditsRequest` (multipart fields plus `image[]`, optional `mask`, `input_fidelity`) — implemented as `V1ImagesGenerationsRequest` and `V1ImagesEditsForm` in `app/core/openai/images.py` (kept alongside `v1_requests.py` to mirror the existing pattern instead of `app/modules/proxy/schemas.py`).
- [x] 1.2 Add `V1ImageResponse` (`{created, data: [{b64_json, revised_prompt}], usage}`) — `V1ImageResponse` / `V1ImageData` / `V1ImageUsage`. Streaming event payloads (`image_generation.partial_image`, `image_generation.completed`, `error`) are emitted by the SSE translator in `app/modules/proxy/images_service.py`.
- [x] 1.3 Add per-model validation matrix in `validate_image_request_parameters`:
  - [x] 1.3.1 `gpt-image-2`: quality, size constraints, reject `input_fidelity`, reject `background=transparent`.
  - [x] 1.3.2 `gpt-image-1.5` / `gpt-image-1` / `gpt-image-1-mini`: fixed sizes; `input_fidelity` only on edits.
  - [x] 1.3.3 Non-`gpt-image-*` model rejected with `invalid_request_error` / `param: model`.
  - [ ] 1.3.4 Relax non-streaming `n > 1` validation up to `images_max_n` while keeping `stream=true && n > 1` rejected until streaming fan-out is specified.

## 2. API Routes

- [x] 2.1 `@v1_router.post("/images/generations")` (JSON in, JSON or `text/event-stream` out depending on `stream`).
- [x] 2.2 `@v1_router.post("/images/edits")` (multipart/form-data with repeatable `image` and optional `mask`).
- [x] 2.3 Routes use the same `ProxyContext`, `validate_proxy_api_key`, `validate_model_access`, and `_enforce_request_limits` plumbing as `/v1/responses`. The blanket `_is_protected_api_path` firewall guard already covers `/v1/*`, so no extra API firewall allowlist entries are needed.
- [x] 2.4 `POST /v1/images/variations` returns 404 with OpenAI `not_found_error`.

## 3. Service Adapter

- [x] 3.1 `_proxy_images_generation_request` and `_proxy_images_edit_request` in `app/modules/proxy/api.py` construct a `ResponsesRequest` with `tools: [{"type": "image_generation", ...}]` and route through `ProxyService.stream_responses`. Translation helpers live in `app/modules/proxy/images_service.py` (kept separate to avoid further bloating `service.py`).
- [x] 3.2 `settings.images_host_model` (default `gpt-5.5`) selects the host model. The host model is hidden from clients (only the requested `gpt-image-*` value appears in the public response).
- [x] 3.3 Compact instructions + a single user `message` with `input_text` (and `input_image` data URLs for edits) deterministically force one `image_generation` tool call.
- [x] 3.4 Non-streaming responses: drain the upstream stream via `collect_responses_stream_for_images`, then `images_response_from_responses` extracts `image_generation_call` items and `tool_usage.image_gen`.
- [x] 3.5 Streaming responses: `translate_responses_stream_to_images_stream` maps upstream events per §6.
- [x] 3.6 Upstream errors / `image_generation_call.status == "failed"` map to OpenAI error envelopes (`content_policy_violation` / `image_generation_failed` / `rate_limit_exceeded` / `server_error`).
- [ ] 3.7 Add non-streaming fan-out orchestration for `/v1/images/generations` and `/v1/images/edits`: one internal Responses request per requested image, deterministic output ordering, and single public error envelope on the first failed internal call.

## 4. Usage and Limits

- [x] 4.1 Image calls are recorded under the publicly-requested `gpt-image-*` model in `request_logs`. They flow through the same `request_logs` table as other requests; the dedicated `image_generation` request *kind* in usage history is intentionally postponed because the existing schema has no `kind` column — model-scoped views already separate image traffic.
- [x] 4.2 `tool_usage.image_gen.input_tokens` and `output_tokens` are surfaced verbatim on the public `usage` block. No size×quality fallback estimate is implemented (and it isn't required: upstream consistently emits the block).
- [x] 4.3 `validate_model_access` runs against the public `gpt-image-*` value before the host-model swap, so API-key allowed-models policy works correctly.
- [ ] 4.4 Surface image usage in `/v1/usage` and the dashboard usage views — not required as a separate code path because usage already groups by `model`. Dashboard model breakdown will display `gpt-image-2` automatically.
- [ ] 4.5 Aggregate `tool_usage.image_gen` token counts across non-streaming fan-out calls and finalize API-key usage once against the public `gpt-image-*` model.

## 5. Configuration and Observability

- [x] 5.1 Settings added in `app/core/config/settings.py`: `images_host_model`, `images_default_model`, `images_max_partial_images`, `images_max_n`.
- [ ] 5.2 Structured logs / Prometheus metrics for image routes — deferred. Existing per-route logging (`proxy_error_response`, request log row) covers basic observability.
- [ ] 5.3 OpenAPI doc updates — not done explicitly; FastAPI generates the route docs automatically because both endpoints use typed Pydantic models (and the multipart Form parameters).

## 6. SSE Translation Matrix

- [x] 6.1 `response.image_generation_call.partial_image` → `image_generation.partial_image` with `b64_json`, `partial_image_index`, `size`, `quality`, `background`, `output_format`, `output_index`. `sequence_number` / `item_id` are dropped.
- [x] 6.2 `response.output_item.done` (`item.type == image_generation_call`) → `image_generation.completed` with `b64_json`, `revised_prompt`, `size`, `quality`, `background`, `output_format`. Stream terminates with `data: [DONE]`.
- [x] 6.3 Reasoning, content_part, output_text, and other Responses-internal events are dropped.
- [x] 6.4 `response.failed`, upstream `error`, and connection truncation surface as a single `error` event followed by `data: [DONE]`.

## 7. Tests

- [x] 7.1 `tests/unit/test_images_translation.py::TestImagesGenerationToResponsesRequest` and `TestImagesEditToResponsesRequest`.
- [x] 7.2 `tests/unit/test_images_translation.py::TestImagesResponseFromResponses` (single-image, multi-image, failed, missing fields).
- [x] 7.3 `tests/unit/test_images_translation.py::TestTranslateResponsesStreamToImagesStream` (success, multi-partial, failed, truncated, error event passthrough) plus integration streaming test.
- [x] 7.4 `tests/unit/test_images_schemas.py` covers the validation matrix with rejection assertions.
- [x] 7.5 `tests/integration/test_proxy_images.py` exercises the route end-to-end with a fake upstream Responses stream (mirrors the existing `core_stream_responses` monkeypatch pattern). Live-account marker not added — follow the same opt-in pattern as `tests/integration/test_proxy_responses.py` if/when the team wants a full live-call assertion.
- [ ] 7.6 Add schema tests for `n > images_max_n`, non-streaming `n > 1`, and streaming `n > 1` rejection.
- [ ] 7.7 Add unit/integration tests proving non-streaming generation/edit fan-out preserves order, aggregates usage, rewrites request-log models for all internal calls, and fails as one public error envelope on internal-call failure.

## 8. Verification

- [x] 8.1 `pytest tests/unit/test_images_schemas.py tests/unit/test_images_translation.py tests/integration/test_proxy_images.py` — 118 passed.
- [x] 8.2 `ruff check app tests` — clean.
- [ ] 8.3 `openspec validate --strict --specs` — CLI not available in the workspace; deferred.
- [ ] 8.4 Empirical dev-container hit — pending (the dev container requires an API key the worker did not have access to). The mocked integration stream matches the empirical event sequence the parent agent verified.
- [ ] 8.5 Fan-out verification: `uv run pytest tests/unit/test_images_schemas.py tests/unit/test_images_translation.py tests/integration/test_proxy_images.py -q`.
