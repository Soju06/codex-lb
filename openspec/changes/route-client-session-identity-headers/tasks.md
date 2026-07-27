# Tasks

## 1. Session identity recognition

- [x] 1.1 Add `x-session-affinity`, `x-session-id`, `x-opencode-session`, `x-claude-code-agent-id`, and `x-claude-remote-session-id` to the session-affinity header list, after the Codex names.
- [x] 1.2 Document the parent-header and request-id exclusions at the definition site.
- [x] 1.3 Recognize client-specific session identity on every Responses API entry point independently of the Codex-only affinity switch.

## 2. Replay hygiene

- [x] 2.1 Strip the new headers in the account-neutral replay header filter.
- [x] 2.2 Strip client session identity at Responses upstream egress only; internal filters preserve the headers so request-log conversation metadata and session affinity still observe them, while non-Responses protocol metadata remains unchanged.

## 3. One-shot side-call bypass

- [x] 3.1 Bypass the bridge (raw HTTP upstream) for session-identified, tool-less, self-contained one-shots; exclude forwarded requests, native Codex clients, anonymous requests, and explicit `websocket` transport.
- [x] 3.2 Normalize the `tools: {}` wire shape (OpenCode title/compaction side calls) to `tools: []` on both `/responses` validation paths so empty tool maps reach the bypass; keep non-empty tool maps rejected.

## 4. Tests

- [x] 4.1 Recognition, precedence, and exclusion coverage for the new headers.
- [x] 4.2 Account-neutral replay strip coverage for the new headers.
- [x] 4.3 One-shot predicate coverage: side calls bypass; tools, anchors, files, native Codex, forwarded, and anonymous requests keep the bridge.
- [x] 4.4 Empty tool map coverage: `tools: {}` validates as tool-less at the `/responses` route and reaches the one-shot bypass; non-empty tool maps stay rejected.
- [x] 4.5 Public Responses affinity coverage: client identity overrides shared prompt-cache affinity while Codex session affinity is disabled.
- [x] 4.6 Replica forwarding coverage: internal owner-forward headers retain client identity until Responses upstream egress.
