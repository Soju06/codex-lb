## Why

Public `/v1/responses` streams can synthesize a terminal `response.failed`
after an upstream stream or bridge ends without `response.completed`. Those
synthetic events currently omit `sequence_number`. OpenAI SDK clients that
require a numeric sequence classify the terminal failure as an unknown chunk,
then report a clean EOF with an unknown finish reason instead of the proxy
error.

## What Changes

- Track the next public Responses sequence while normalizing SSE events.
- Assign the next numeric sequence to terminal `response.failed` events that
  omit it or carry a non-numeric value.
- Give a synthesized leading `response.created` and its following failure
  distinct consecutive sequences.
- Preserve valid upstream sequence numbers and Codex-private stream behavior.
- Cover bridge failures after reasoning output and reused bridge sessions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: synthetic public terminal failures remain parseable
  by strict OpenAI SDK Responses clients.

## Impact

- Code: `app/modules/proxy/api.py`
- Tests: `tests/unit/test_proxy_api_responses_contract.py`,
  `tests/integration/test_http_responses_bridge.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`
