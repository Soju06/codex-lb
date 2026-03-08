## Why

`/v1/responses` non-streaming requests are currently executed as upstream SSE streams and then collapsed into a single JSON response by reading only the terminal `response.completed` or `response.incomplete` event. When the upstream emits reasoning or other rich output items in intermediate events such as `response.output_item.done`, those items are dropped from the final JSON response if the terminal event omits `response.output`.

This makes `codex-lb` lose reasoning-related payloads that clients expect from the v1 Responses API surface.

## What Changes

- Reconstruct non-streaming `/v1/responses` output items from upstream SSE item events before returning the final JSON response.
- Preserve raw output item payloads, including reasoning-related fields, when the terminal response omits or leaves `output` empty.
- Add regression coverage for reasoning-style output item preservation in non-streaming `/v1/responses`.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: preserve output items from streamed upstream Responses events when serving non-streaming `/v1/responses`.

## Impact

- **Code**: `app/modules/proxy/api.py`
- **Tests**: `tests/integration/test_proxy_responses.py`
