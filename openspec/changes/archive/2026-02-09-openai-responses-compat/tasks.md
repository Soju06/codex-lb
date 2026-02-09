## 1. Validation & Schema Alignment

- [x] 1.1 Expand Responses request schema to validate `input` types, `conversation` vs `previous_response_id`, and `truncation` rules
- [x] 1.2 Enforce strict `store=false` and invalid-parameter errors in Responses requests
- [x] 1.3 Expand Chat Completions request schema to validate message content types and role constraints
- [x] 1.4 Add response_format/json_schema validation including missing schema and name constraints
- [x] 1.5 Enforce Responses `include` allowlist per official docs (reject unknown include values)

## 2. Responses Streaming & Non-Streaming Behavior

- [x] 2.1 Normalize upstream SSE into OpenAI Responses event taxonomy (created/in_progress/completed/failed) and data events (output_text.delta, function_call_arguments.delta, refusal.delta)
- [x] 2.2 Emit response.failed on missing terminal events and map upstream errors to OpenAI envelopes with incomplete_details
- [x] 2.3 Add non-streaming Responses aggregation to return a single response object
- [x] 2.4 Preserve tool call output items and usage fields in both streaming and non-streaming paths
- [x] 2.5 Validate and forward response.failed reasons per streaming spec

## 3. Chat Completions Mapping & Streaming

- [x] 3.1 Map chat messages to Responses input/instructions with normalized tool definitions and tool_choice values
- [x] 3.2 Emit chat.completion.chunk streams with correct role, tool_call deltas, and [DONE]
- [x] 3.3 Support stream_options.include_usage usage chunk (empty choices final chunk)
- [x] 3.4 Support stream_options.include_obfuscation pass-through behavior
- [x] 3.5 Enforce image size limits and audio format validation for message content parts
- [x] 3.6 Build non-streaming chat completion responses with tool_calls and finish_reason mapping

## 4. Error Handling & Compatibility Guards

- [x] 4.1 Standardize OpenAI error envelopes (type/code/param) for validation failures
- [x] 4.2 Add explicit unsupported-feature errors (e.g., store, unsupported content types)
- [x] 4.3 Add truncation overflow handling per truncation=disabled/auto
- [x] 4.4 Align HTTP error status mapping with OpenAI error code guidance (401/403/429/5xx)

## 5. Tests & Client Compatibility

- [x] 5.1 Unit tests for request validation (Responses and Chat) including edge cases
- [x] 5.2 Streaming tests for Responses event sequences (output_text.delta, function_call_arguments.delta, refusal.delta) and chat chunk mapping
- [x] 5.3 Integration tests for non-streaming Responses and chat completions
- [x] 5.4 Wire-compat tests using official OpenAI client against local endpoint
- [x] 5.5 Codex client (./refs/codex) compatibility tests for Responses proxy behavior
- [x] 5.6 Edge-case tests for Responses include allowlist and response.failed incomplete_details
- [x] 5.7 Edge-case tests for chat stream_options and oversized image/audio inputs

## 6. Documentation & Rollout

- [x] 6.1 Document unsupported features and error codes for backend limitations
- [x] 6.2 Add release notes and rollout checklist (monitor new error patterns)
