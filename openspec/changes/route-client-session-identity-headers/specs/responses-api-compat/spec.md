## ADDED Requirements

### Requirement: Tool-less one-shot requests bypass the HTTP bridge

When the HTTP responses bridge is enabled, a request whose session identity comes from a client-declared identity header (`x-session-affinity`, `x-session-id`, `x-opencode-session`, `x-claude-code-agent-id`, `x-claude-remote-session-id`, with no Codex-name session header present) and that is self-contained and tool-less — no `tools`, no `previous_response_id`, no incoming turn-state header, no nonblank `conversation`, and no input file references — MUST bypass the bridge for that request only and be sent over raw HTTP upstream, provided the request is not a forwarded bridge request, does not originate from a native Codex client (native Codex clients keep websocket-mode behavior), and `upstream_stream_transport` is not explicitly `websocket`. Requests without a session identity header, and requests carrying a Codex-name session header (`session_id`, `session-id`, `x-codex-session-id`, `x-codex-conversation-id`, `thread-id` — bridge-centric Codex-protocol flows), MUST keep their existing bridge behavior. Agent clients send such side calls (title generation, summaries, compaction) on the same session identity as their agent turns; routing them through the bridge would fork an independent bridge lane per overlap with the agent's in-flight turn while gaining nothing from a persistent WebSocket.

#### Scenario: Title-generation side call skips the bridge

- **GIVEN** the HTTP responses bridge is enabled and `upstream_stream_transport` is `auto`
- **AND** an OpenCode title-generation request arrives with session identity headers, no tools, and no continuity anchors
- **WHEN** the request is routed
- **THEN** the bridge is bypassed for that request and it is sent over raw HTTP upstream
- **AND** no bridge session or fork lane is created for it

#### Scenario: Agent turns with tools keep the bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a request carrying tool definitions arrives on the same session identity
- **THEN** the request routes through the bridge as before

#### Scenario: Native Codex clients keep websocket-mode behavior

- **GIVEN** a request whose `originator` header names a native Codex client
- **WHEN** the request is tool-less and unanchored
- **THEN** the bridge is not bypassed for that request

#### Scenario: Explicit websocket transport keeps the bridge

- **GIVEN** `upstream_stream_transport` is explicitly `websocket`
- **WHEN** a tool-less self-contained request arrives
- **THEN** the bridge is not bypassed for that request

#### Scenario: Anonymous requests keep existing bridge behavior

- **GIVEN** a tool-less self-contained request with no session identity header
- **WHEN** the request is routed
- **THEN** the bridge is not bypassed for that request

#### Scenario: Codex-name session headers keep existing bridge behavior

- **GIVEN** a tool-less self-contained request carrying a `session_id` or `thread-id` header
- **WHEN** the request is routed
- **THEN** the bridge is not bypassed for that request
