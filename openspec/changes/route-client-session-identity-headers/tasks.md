# Tasks

## 1. Session identity recognition

- [x] 1.1 Add `x-session-affinity`, `x-session-id`, `x-opencode-session`, `x-claude-code-agent-id`, and `x-claude-remote-session-id` to the session-affinity header list, after the Codex names.
- [x] 1.2 Document the parent-header and request-id exclusions at the definition site.

## 2. Replay hygiene

- [x] 2.1 Strip the new headers in the account-neutral replay header filter.

## 3. One-shot side-call bypass

- [x] 3.1 Bypass the bridge (raw HTTP upstream) for session-identified, tool-less, self-contained one-shots; exclude forwarded requests, native Codex clients, anonymous requests, and explicit `websocket` transport.

## 4. Tests

- [x] 4.1 Recognition, precedence, and exclusion coverage for the new headers.
- [x] 4.2 Account-neutral replay strip coverage for the new headers.
- [x] 4.3 One-shot predicate coverage: side calls bypass; tools, anchors, files, native Codex, forwarded, and anonymous requests keep the bridge.
