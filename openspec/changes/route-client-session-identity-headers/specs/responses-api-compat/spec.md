## ADDED Requirements

### Requirement: Tool-less one-shot requests bypass the HTTP bridge

When the HTTP responses bridge is enabled, a request whose session identity comes from a client-declared identity header (`x-session-affinity`, `x-session-id`, `x-opencode-session`, `x-claude-code-agent-id`, `x-claude-remote-session-id`, with no Codex-name session header present) and that is self-contained and tool-less — no `tools`, no `previous_response_id`, no incoming turn-state header, no nonblank `conversation`, no stored `prompt`, no input file references, and no account-scoped hosted input items — MUST bypass the bridge for that request only and be sent over raw HTTP upstream, provided the request is not a forwarded bridge request, does not originate from a native Codex client (native Codex clients keep websocket-mode behavior), `upstream_stream_transport` is not explicitly `websocket`, and an `auto` upstream transport does not have an effective `always_websocket` downstream policy. The effective downstream policy MUST apply the existing per-API-key override precedence. Requests without a session identity header, and requests carrying a Codex-name session header (`session_id`, `session-id`, `x-codex-session-id`, `x-codex-conversation-id`, `thread-id` — bridge-centric Codex-protocol flows), MUST keep their existing bridge behavior. Agent clients send such side calls (title generation, summaries, compaction) on the same session identity as their agent turns; routing them through the bridge would fork an independent bridge lane per overlap with the agent's in-flight turn while gaining nothing from a persistent WebSocket.

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

#### Scenario: Always-websocket policy keeps the bridge

- **GIVEN** `upstream_stream_transport` is `auto`
- **AND** the effective global or per-API-key `http_downstream_transport_policy` is `always_websocket`
- **WHEN** a tool-less self-contained request arrives
- **THEN** the bridge is not bypassed for that request

#### Scenario: Account-scoped hosted input keeps the bridge

- **GIVEN** a client-identified tool-less request containing `item_reference` or `file_search_call`
- **WHEN** the request is routed
- **THEN** the bridge is not bypassed
- **AND** bare-session cap spillover to another account is disabled

#### Scenario: Stored prompt keeps the bridge

- **GIVEN** a client-identified tool-less request containing a non-empty stored `prompt`
- **WHEN** the request is routed
- **THEN** the bridge is not bypassed
- **AND** bare-session cap spillover to another account is disabled

#### Scenario: Anonymous requests keep existing bridge behavior

- **GIVEN** a tool-less self-contained request with no session identity header
- **WHEN** the request is routed
- **THEN** the bridge is not bypassed for that request

#### Scenario: Codex-name session headers keep existing bridge behavior

- **GIVEN** a tool-less self-contained request carrying a `session_id` or `thread-id` header
- **WHEN** the request is routed
- **THEN** the bridge is not bypassed for that request

### Requirement: Empty tool map normalizes to an empty tool list

`/responses` request validation MUST treat a `tools` field sent as an empty JSON object (`tools: {}`) as equivalent to `tools: []` on both the codex-native and OpenAI-compat validation paths, so such requests validate and count as tool-less (including for the one-shot bypass predicate). OpenCode's title and compaction side calls declare tool-lessness with this empty-map wire shape. Non-empty tool maps MUST continue to be rejected as invalid payloads.

#### Scenario: OpenCode empty tool map side call validates as tool-less

- **GIVEN** an OpenCode title or compaction side call whose body carries `tools: {}` and client-declared session identity headers
- **WHEN** the request is validated at `/responses`
- **THEN** validation succeeds and the request's tool list is empty
- **AND** the request is treated as tool-less by the one-shot bypass predicate

#### Scenario: Non-empty tool maps stay rejected

- **GIVEN** a `/responses` request whose `tools` field is a non-empty JSON object
- **WHEN** the request is validated
- **THEN** the request is rejected as an invalid payload
