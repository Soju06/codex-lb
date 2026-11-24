## Why

A raw HTTP request can receive an upstream quota rejection, exclude its serving account and still remain bound to that same account. Codex then reports a usage limit while other accounts remain available. The rejection occurs for normal inline history such as compaction, reasoning, subagent messages, namespaced calls, host events and empty tool-output parts.

## What Changes

- Default to retrying the original stateless request unchanged after a quota rejection before downstream output.
- Block migration for explicitly account-owned continuation or hosted resource references and keep single-account routing and visible-output safeguards.
- Allow eligible stateless requests to leave a stale hard session binding when its account hits quota or cannot be selected.
- Extend the account-attempt budget only as distinct accounts reject the request for quota, up to 64 accounts or the existing deadline. Other failure types retain the original attempt budget.
- Log an explicit reason when a known account-owned resource blocks migration.

## Impact

Raw Responses HTTP streaming. No configuration, persistent transcript store, schema migration, credentials, or changes to WebSocket ownership. Related to #2068 and #2069; this does not close those issues because their other transports and owner-anchored cases are out of scope.
