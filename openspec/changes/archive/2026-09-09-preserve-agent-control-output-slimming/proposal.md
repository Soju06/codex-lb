## Why

Response-create slimming can replace a completed agent-control result with an
omission notice. The next model turn then loses the state returned by a
namespaced collaboration wait or other agent-control call.

## What Changes

- Preserve a historical `function_call_output` or `custom_tool_call_output`
  only when its matching pending call has the `collaboration` or
  `multi_agent_v1` namespace.
- Apply the same call-ID-based rule to the bridge/service and direct WebSocket
  response-create slim paths, classifying only the original historical prefix
  before outbound normalization strips replay namespaces.
- Keep unrelated oversized outputs, including bare-name user tools, eligible
  for the existing omission policy.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: response-create slimming preserves the completed
  state of namespaced agent-control calls on every live upstream path.

## Impact

Touches the response-create slim helpers in the proxy service and core WebSocket
client plus focused unit coverage. No protocol serialization, fallback, or
archived specification changes are included.
