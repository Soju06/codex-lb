## 1. Implementation

- [x] 1.1 Accept self-contained `tool_search_call` / `tool_search_output`
  pairs in replay-safety validation.
- [x] 1.2 Require portable tool-search calls and outputs to be client-executed
  when an execution owner is present.
- [x] 1.3 Reject duplicate or non-terminal compact triggers before upstream
  forwarding while preserving one terminal trigger.

## 2. Regression coverage

- [x] 2.1 Cover tool-search replay in account-neutral predicates.
- [x] 2.2 Cover HTTP bridge and WebSocket trimming of replayed tool-search
  calls.
- [x] 2.3 Cover nested encrypted compaction inside tool-search arguments.
- [x] 2.4 Cover server-executed tool-search outputs failing closed.
- [x] 2.5 Cover compact-trigger duplicate rejection and single-trigger
  forwarding.

## 3. Validation

- [x] 3.1 Run focused proxy replay and compact-trigger tests.
- [ ] 3.2 Run strict OpenSpec validation for this change. (Blocked locally:
  `uv run openspec validate preserve-tool-search-replay-semantics --strict`
  cannot spawn `openspec`; the CLI is unavailable in this shell.)
