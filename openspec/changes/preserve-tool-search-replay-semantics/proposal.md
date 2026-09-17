# Why

Codex replay can include completed `tool_search_call` and
`tool_search_output` items. On `main` the owner-unavailable projection drops
those pairs, so a replacement account never sees the discovered tools and the
next turn fails with `No tool output found for tool search call`. Those
histories are safe to replay across accounts only when they are
client-executed and every discovered tool declaration is itself
account-neutral. The proxy also needs a clear compact-trigger compatibility
rule: one terminal trigger is forwarded, the canonical compact route rejects
duplicate or non-terminal triggers before upstream work, and the V1 route keeps
duplicate-terminal-trigger normalization while still rejecting a trigger hidden
behind a trailing developer message.

# What Changes

- Treat completed client-executed tool-search call/output pairs as eligible
  for account-neutral replay instead of omitting them from the projection.
- Validate `tool_search_output.tools` in the shape Codex serializes
  (`LoadableToolSpec`): `function`/`custom` declarations pass the existing
  declared-tool rules (optional boolean `defer_loading`), and a `namespace`
  may group such declarations. String `output`, missing `tools`, failed
  status, server execution, MCP/hosted/container-bound declarations, and
  nested namespaces fail closed.
- Strip response-owned tool-search item ids before HTTP bridge and WebSocket
  fresh retries, keeping the anchored upstream body prefix-trimmed.
- Reject a V1 compact request whose trigger is not the last top-level item
  before the trailing-message hoist can normalize it away.

# Reach

The pair is retained only when the rest of the request is already
account-neutral. Responses-Lite requests that declare `tool_search` /
`namespace` tools in `additional_tools`, and non-Lite requests declaring
`{"type": "tool_search"}` or `defer_loading` function tools at top level, still
fail the declared-tool allowlists and stay owner-bound; extending those
allowlists is a separate spec-bearing change.

# Capabilities

## Modified Capabilities

- `responses-api-compat`: replay-safety and compact-trigger validation now
  define the portable tool-search and trigger shapes, and the
  owner-unavailable projection retains validated tool-search pairs.

# Impact

HTTP bridge and WebSocket retries can preserve self-contained tool-search
history without redispatching the call, while server-owned or non-neutral
tool-search state and ambiguous compact triggers stay on the original owner
path or fail before upstream dispatch. Clients sending a V1 compact request
with a trigger followed by a trailing developer message now receive HTTP 400
instead of a silently normalized request.
