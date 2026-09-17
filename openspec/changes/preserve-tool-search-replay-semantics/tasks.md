## 1. Implementation

- [x] 1.1 Accept self-contained `tool_search_call` / `tool_search_output`
  pairs in replay-safety validation and keep them in the owner-unavailable
  projection with ids stripped.
- [x] 1.2 Require portable tool-search calls and outputs to be client-executed
  when an execution owner is present.
- [x] 1.3 Run every `tool_search_output.tools` element through the declared-tool
  rules, with a `namespace` branch for grouped declarations, and reject the
  string `output` shape.
- [x] 1.4 Reject duplicate or non-terminal compact triggers before upstream
  forwarding while preserving one terminal trigger, including the V1
  pre-hoist check.

## 2. Regression coverage

- [x] 2.1 Cover tool-search replay in account-neutral predicates with the
  upstream `LoadableToolSpec` JSON shape.
- [x] 2.2 Cover HTTP bridge and WebSocket trimming of replayed tool-search
  calls.
- [x] 2.3 Cover nested encrypted compaction inside tool-search arguments.
- [x] 2.4 Cover server-executed, failed, string-`output`, MCP, container-bound,
  and nested-namespace tool-search outputs failing closed.
- [x] 2.5 Cover compact-trigger duplicate rejection, single-trigger
  forwarding, and the V1 hidden non-terminal trigger.
- [x] 2.6 Cover WebSocket owner-loss failover delivering the upstream-shaped
  tool-search pair to the replacement account.

## 3. Validation

- [x] 3.1 Run focused proxy replay and compact-trigger tests.
- [x] 3.2 Run strict OpenSpec validation for this change.
