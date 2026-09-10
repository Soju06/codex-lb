# Why

Codex replay can include completed `tool_search_call` and
`tool_search_output` items. Those histories are safe to replay across accounts
only when they are client-executed and self-contained. The proxy also needs a
clear compact-trigger compatibility rule: one terminal trigger is forwarded,
while the canonical compact route rejects duplicate or non-terminal triggers
before upstream work and the V1 route keeps duplicate-terminal-trigger
normalization for compatible clients.

# What Changes

- Treat completed client-executed tool-search call/output pairs as eligible
  for account-neutral replay.
- Reject server-executed tool-search outputs from portable replay, even when
  their `tools` field is otherwise well formed.
- Keep compact-trigger forwarding compatible for a single terminal trigger,
  fail closed for ambiguous canonical triggers, and preserve V1 duplicate
  terminal trigger normalization.

# Capabilities

## Modified Capabilities

- `responses-api-compat`: replay-safety and compact-trigger validation now
  define the portable tool-search and trigger shapes.

# Impact

HTTP bridge and WebSocket retries can preserve self-contained tool-search
history without redispatching the call, while server-owned tool-search state and
ambiguous compact triggers stay on the original owner path or fail before
upstream dispatch.
