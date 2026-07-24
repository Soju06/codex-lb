## ADDED Requirements

### Requirement: Client-declared session identity headers key bare process-session affinity

The service MUST recognize client-declared session identity headers as the bare process-session affinity key, checked in this precedence order: `session_id`, `session-id`, `x-codex-session-id`, `x-codex-conversation-id`, `thread-id` (the Codex CLI names, unchanged and first), then `x-session-affinity`, `x-session-id`, `x-opencode-session`, `x-claude-code-agent-id`, `x-claude-remote-session-id`. The first header present with a nonblank value MUST supply the key, so a request carrying both a Codex name and a client identity header routes exactly as before this requirement existed. Sessions keyed by these headers MUST carry the same bare process-session semantics defined elsewhere in this capability: soft locality, cap spillover for self-contained payloads, and no ownership evidence.

Parent identity headers — `x-parent-session-id`, `x-codex-parent-thread-id`, `x-claude-code-parent-agent-id`, and `x-openai-subagent` — MUST NOT supply the session affinity key: a parent key would collapse every subagent of one parent onto a single session. `x-client-request-id` MUST NOT supply the key because clients also populate it with per-request identifiers.

The account-neutral replay header filter MUST strip the recognized client identity headers alongside the Codex names so a replay dispatched to a fresh account cannot re-register the downstream alias. The recognized client identity headers MUST NOT be forwarded upstream.

#### Scenario: Subagent sessions route independently

- **GIVEN** two OpenCode subagent requests that share a system-prompt prefix but carry distinct `x-session-affinity` values
- **WHEN** each subagent's first turn selects an account
- **THEN** each request keys its own bare process-session affinity
- **AND** neither request collapses onto the other's account via derived prompt-cache affinity

#### Scenario: Codex names keep precedence

- **GIVEN** a request carrying both `session_id` and `x-session-affinity`
- **WHEN** the session affinity key is derived
- **THEN** the `session_id` value supplies the key

#### Scenario: Parent identity does not key affinity

- **GIVEN** a request carrying only `x-parent-session-id`, `x-codex-parent-thread-id`, `x-claude-code-parent-agent-id`, or `x-openai-subagent`
- **WHEN** the session affinity key is derived
- **THEN** no session affinity key is produced

#### Scenario: Account-neutral replay strips client identity headers

- **GIVEN** an account-neutral replay for a session keyed by `x-session-affinity`
- **WHEN** the replay's session-creation headers are built
- **THEN** every recognized client identity header is absent
- **AND** unrelated headers such as `user-agent` are preserved
