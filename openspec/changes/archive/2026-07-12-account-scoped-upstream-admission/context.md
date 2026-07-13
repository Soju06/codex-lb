# Context

The proxy should maintain a hard distinction between upstream-account state and local advisory data:

- Upstream/account state: statuses produced by selected-account upstream responses, authentication state, explicit operator account pause/deactivation, and account-local concurrency caps
- Advisory local data: usage snapshots, synthetic planner costs, budget pressure, and dashboard estimates

Foreground user traffic should fail open toward upstream unless blocked by upstream-proven account state, authentication/operator state, explicit API-key policy, or local capacity. Opportunistic/synthetic traffic may remain more conservative because it is an explicit local burn policy rather than a normal user request path.
