# Responses API Compatibility Context

## Purpose and Scope

This capability implements OpenAI-compatible behavior for `POST /v1/responses`, including request validation, streaming events, non-streaming aggregation, and OpenAI-style error envelopes. The scope is limited to what the ChatGPT upstream can provide; unsupported features are explicitly rejected.

See `openspec/specs/responses-api-compat/spec.md` for normative requirements.

## Rationale and Decisions

- **Responses as canonical wire format:** Internally we treat Responses as the source of truth to avoid divergent streaming semantics.
- **Strict validation:** Required fields and mutually exclusive fields are enforced up front to match official client expectations.
- **No truncation support:** Requests that include `truncation` are rejected because upstream does not support it.

## Constraints

- Upstream limitations determine available modalities, tool output, and overflow handling.
- `store=true` is accepted and persisted in local response context storage.
- `include` values must be on the documented allowlist.
- `previous_response_id` is resolved locally from response context storage.
- `truncation` is rejected.
- `/v1/responses/compact` is supported only when the upstream implements it.
- Local response context durability uses TTL-bound storage with periodic background cleanup.
- Reference resolution is API-key scoped by default; optional global fallback can be enabled via settings.

## Include Allowlist (Reference)

- `code_interpreter_call.outputs`
- `computer_call_output.output.image_url`
- `file_search_call.results`
- `message.input_image.image_url`
- `message.output_text.logprobs`
- `reasoning.encrypted_content`
- `web_search_call.action.sources`

## Failure Modes

- **Stream ends without terminal event:** Emit `response.failed` with `stream_incomplete`.
- **Upstream error / no accounts:** Non-streaming responses return an OpenAI error envelope with 5xx status.
- **Invalid request payloads:** Return 4xx with `invalid_request_error`.
- **Missing local response reference:** Return 404 with `not_found` and a message indicating `store=true` for persistence.

## Error Envelope Mapping (Reference)

- 401 → `invalid_api_key`
- 403 → `insufficient_permissions`
- 404 → `not_found`
- 429 → `rate_limit_exceeded`
- 5xx → `server_error`

## Examples

Non-streaming request/response:

```json
// request
{ "model": "gpt-5.1", "input": "hi" }
```

```json
// response
{ "id": "resp_123", "object": "response", "status": "completed", "output": [] }
```

## Operational Notes

- Pre-release: run unit/integration tests and optional OpenAI client compatibility tests.
- Smoke tests: stream a response, validate non-stream responses, and verify error envelopes.
- Post-deploy: monitor `no_accounts`, `stream_incomplete`, and `upstream_unavailable`.
