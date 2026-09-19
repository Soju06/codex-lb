<!-- agent-lb:coding-agent-routing:start -->

## Coding-agent routing

Canon: `~/.agents/policy/coding-agents/ROUTING.md`; the seat table is
`~/.agent-lb/managed/coding-agents/routing-table.json` and `route pick <class>`
reads it. Fable drives (decide, dispatch, reconcile, accept); seats do volume.
Opus 5 (`opus-seat`) is the default seat and is not rationed; Fable is the
binding pool, so nothing but the driver runs on it. Verification is
cross-vendor (`route pick verify --author-vendor <vendor>`). `route pools`
shows live headroom per pool; `route doctor` writes the live fallback chain.
Every dispatch and closeout is recorded in `~/.claude/logs/dispatch.jsonl`;
defaults change only from that record (`route learn`). A closeout is a claim;
check acceptance yourself.

<!-- agent-lb:coding-agent-routing:end -->
