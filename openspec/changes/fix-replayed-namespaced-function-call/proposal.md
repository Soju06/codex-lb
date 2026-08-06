## Why

Replaying a namespaced Responses API tool call currently forwards its local-only `namespace` field to OpenAI, which rejects the request with `Unknown parameter: input[*].namespace`. Codex 0.146 emits this shape for both namespaced `function_call` and `custom_tool_call` history. The proxy must restore upstream compatibility without losing the namespace identity used for local side-effect replay deduplication.

## What Changes

- Remove `namespace` only from recognized replayed tool-call `input` items when building the upstream wire payload.
- Preserve the validated request input, including namespace metadata, for local deduplication and continuity processing.
- Preserve client-provided top-level namespace tool definitions byte-identically.
- Apply the same outbound normalization to standard and compact Responses requests.
- Apply namespace-only normalization to configured OpenAI-compatible Responses model-source egress.
- Preserve namespace metadata while classifying HTTP bridge requests for cross-account replay safety.
- Add regression coverage at request serialization and the public HTTP, model-source, and WebSocket Responses proxy paths.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define upstream wire compatibility for replayed namespaced tool calls while preserving local call identity and top-level tool definitions.

## Impact

- Affects Responses request serialization in `app/core/openai/requests.py`.
- Adds unit, `/v1/responses`, model-source, and WebSocket `response.create` integration regression tests.
- Does not change dependencies, settings, schemas, or user-facing documentation.
