## Context

The bridge/service and direct WebSocket paths each slim historical
`response.create` input before upstream send. The previous façade-only repair
did not protect the direct core path and used a broad pending-call type set.

## Goals / Non-Goals

**Goals:**

- Preserve outputs needed to continue namespaced collaboration and
  multi-agent control flows.
- Make bridge and direct-WebSocket slimming equivalent.
- Retain normal payload reduction for shell and unnamespaced user tools.

**Non-Goals:**

- Do not alter namespace serialization, retry/fallback behavior, or archived
  OpenSpec history.
- Do not protect a tool merely because its name is `wait_agent` or
  `send_input`.

## Decisions

- Derive protected call IDs from historical `function_call` and
  `custom_tool_call` items whose namespace is exactly `collaboration` or
  `multi_agent_v1`; use call ID rather than tool name, because names are
  user-controlled.
- Derive those IDs only from the historical prefix of the original
  `ResponsesRequest.input` before outbound payload normalization removes replay
  namespaces, then carry only the IDs into slimming. Recent calls cannot
  protect historical outputs that reuse the same call ID.
- Pair outputs to calls with the same nearest-preceding-unmatched matcher as
  compact's `_compact_matching_tool_call_index`: each output pairs with the
  closest earlier unmatched call for its `(protocol, call_id)` key and is
  protected only when that paired call is namespaced, so same-protocol
  call-ID reuse cannot exempt unrelated outputs. This deliberately differs
  from a purely positional nth-output/nth-call rule: an orphan output with no
  preceding unmatched call (for example after session-anchor trimming removed
  its call from replay) pairs with nothing, consumes no call, and stays
  slimmable.
- Skip slimming only for matching `function_call_output` and
  `custom_tool_call_output` items. Other output types keep their current
  treatment.
- Keep the detector in the reusable core proxy module and use it from the
  service façade, so the two live paths share the classification rule.

## Risks / Trade-offs

- [A protected result can leave an oversized request above budget] -> existing
  fail-fast `payload_too_large` handling remains authoritative.
- [Malformed or missing IDs cannot be correlated safely] -> they remain
  slimmable under the current policy.
