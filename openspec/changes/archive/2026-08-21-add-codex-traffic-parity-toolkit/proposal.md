## Why

codex-lb can carry Responses traffic over three materially different wire
contracts: ordinary HTTP JSON, HTTP Server-Sent Events, and native Responses
WebSockets. Existing compatibility tests exercise those paths in isolation,
but operators have no repeatable way to capture a direct baseline, the
client-to-LB edge, and the LB-to-upstream edge and then determine whether a
difference is an expected transport or routing transformation versus a proxy
fidelity regression.

claude-lb established a useful three-path capture workflow, SSE parser, and
structural report. Its Anthropic-specific event model and HTTP-only capture
assumptions do not cover Codex Responses WebSockets, so codex-lb needs a native
variant rather than a filename-level port.

## What Changes

- Add a mitmproxy capture addon for Codex Responses HTTP JSON, HTTP SSE, and
  WebSocket frames.
- Normalize each transport into a common turn representation without erasing
  the original transport identity.
- Compare the same-run client-to-LB and LB-to-upstream legs strictly while
  treating a separately generated direct run as a structural baseline.
- Generate machine-readable JSON and a Markdown report that expose transport
  changes, request/event/terminal/usage/tool differences, and missing turns.
- Redact credential headers unconditionally and make metadata-only body
  capture the default so the normal workflow does not persist raw prompts or
  model output.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `compatibility-tooling`: add transport-aware three-path Codex traffic capture
  and parity analysis.

## Impact

Development-only scripts, focused unit tests, generated-output ignore rules,
and an operator/developer guide. Runtime proxy behavior, settings, database
schema, and deployed dependencies do not change; mitmproxy remains an optional
tooling dependency.
