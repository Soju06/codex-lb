# Tasks

## 1. Session identity recognition

- [x] 1.1 Add `x-session-affinity`, `x-session-id`, `x-opencode-session`, `x-claude-code-agent-id`, and `x-claude-remote-session-id` to the session-affinity header list, after the Codex names.
- [x] 1.2 Document the parent-header and request-id exclusions at the definition site.

## 2. Replay hygiene

- [x] 2.1 Strip the new headers in the account-neutral replay header filter.

## 3. Tests

- [x] 3.1 Recognition, precedence, and exclusion coverage for the new headers.
- [x] 3.2 Account-neutral replay strip coverage for the new headers.
