# Route client-declared session identity headers as process-session affinity

## Why

Agent clients identify each session — including every subagent session — with a header on each provider request, but codex-lb's affinity parsing only recognizes the Codex CLI names (`session_id`, `session-id`, `x-codex-session-id`, `x-codex-conversation-id`, `thread-id`). OpenCode sends `x-session-affinity` / `X-Session-Id` (and `x-opencode-session`), OpenClaw sends `x-session-affinity` alongside `session_id`, and Claude Code sends `x-claude-code-agent-id` / `x-claude-remote-session-id` — none recognized for routing (OpenCode's are consumed only for request-log conversation grouping). Unrecognized clients fall through to derived prompt-cache affinity, whose key hashes the shared system-prompt prefix, so every subagent of one parent collapses onto a single sticky account. On a live deployment this concentrated 24 concurrent subagent streams on one account while five other accounts sat nearly idle, because each subagent's turns became continuity-bound to the collapsed account after its first turn.

## What Changes

- The session-affinity header list gains the identity headers the surveyed clients send per session: `x-session-affinity`, `x-session-id`, `x-opencode-session`, `x-claude-code-agent-id`, `x-claude-remote-session-id`. The existing Codex names keep precedence, so no Codex CLI request routes differently.
- Each client session (including each subagent) thereby carries its own bare process-session key: first turns select an account independently under the existing cap-filtered, usage-weighted selection with #1382 bare-session cap spillover, so parallel subagents distribute across eligible accounts instead of collapsing onto one prompt-cache account.
- Parent identity headers (`x-parent-session-id`, `x-codex-parent-thread-id`, `x-claude-code-parent-agent-id`, `x-openai-subagent`) and per-request ids (`x-client-request-id`) are explicitly excluded from session identity: parent keys would re-collapse subagents; request ids are not stable session identity.
- The account-neutral replay header strip set drops the new headers alongside the Codex names so a fresh-account replay cannot re-register the alias.
- None of the new headers are added to the upstream forwarding allowlist; they remain proxy-local.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: Define the recognized client session identity header set, its precedence, and the parent/request-id exclusions for bare process-session affinity.

## Impact

- `app/modules/proxy/affinity.py`: recognized header list.
- `app/modules/proxy/continuity.py`: account-neutral replay strip set.
- Behavior change for OpenCode/OpenClaw/Claude Code-style clients only: requests that previously derived prompt-cache affinity now carry per-session bare affinity. Codex CLI routing is unchanged (its headers keep precedence). Existing sticky rows are unaffected; new sessions simply key by their declared identity.
- No setting, migration, dashboard, or API schema change.
