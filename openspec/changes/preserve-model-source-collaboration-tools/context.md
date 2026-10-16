## Context and rationale

The current source-tool-filtering contract deliberately drops namespace tools
for plain OpenAI-compatible sources. Enabling them for every source would remove
that protection. `multi_agent_version` already declares the model's collaboration
capability to Codex, so it can also enable namespace forwarding without another
setting. A nonblank string is the capability signal; version names are left to
the client and provider rather than restricted to a local v1/v2 allowlist.

For example, a source model with `{"multi_agent_version": "v2"}` retains a
`collaboration` namespace with its nested `spawn_agent` and `wait` definitions.
If the same request contains an unsupported `web_search` tool, that tool and its
search-only `include` entries are still removed. A namespace tool choice stays
intact. Models with absent, blank, or non-string versions continue to require an
explicit `experimental_supported_tools: ["namespace"]` declaration.

The implementation follows the approach shared by issue reporter
[nhdong1993](https://github.com/nhdong1993) in
[commit 00164ab](https://github.com/nhdong1993/codex-lb/commit/00164ab06ccdfc868fa2e04a168a804147606325),
adapted to the current capability filter and regression fixtures.

## Scope and verification limits

This change does not alter namespace stripping on replayed tool-call input,
subscription account forwarding, WebSocket routing, model catalog defaults, or
`base_instructions`. Passing the route tests establishes payload preservation
through a real proxy route and a local upstream stub. It does not establish that
a particular external model executes collaboration calls correctly.

A real Codex parent/child session passed; see [verification.md](verification.md)
for its setup and evidence. Maintainer agreement on the revised namespace
contract remains pending. Keep this proposal active until that decision.
