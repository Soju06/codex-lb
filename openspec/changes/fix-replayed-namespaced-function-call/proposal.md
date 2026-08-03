## Why

Replaying a namespaced Responses API `function_call` currently forwards its local-only `namespace` field to OpenAI, which rejects the request with `Unknown parameter: input[*].namespace`. The proxy must restore upstream compatibility without losing the namespace identity used for local side-effect replay deduplication.

## What Changes

- Remove `namespace` only from replayed `input` items whose type is `function_call` when building the upstream wire payload.
- Preserve the validated request input, including namespace metadata, for local deduplication and continuity processing.
- Preserve client-provided top-level namespace tool definitions byte-identically.
- Apply the same outbound normalization to standard and compact Responses requests.
- Add regression coverage at request serialization and the public Responses proxy path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define upstream wire compatibility for replayed namespaced function calls while preserving local call identity and top-level tool definitions.

## Impact

- Affects Responses request serialization in `app/core/openai/requests.py`.
- Adds unit and `/v1/responses` integration regression tests.
- Does not change dependencies, settings, schemas, or user-facing documentation.
