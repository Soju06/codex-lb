# Design and review decisions

The durable context owner is immutable. Notes calls stay with that account even when inference rotates; history calls query the recorded participants. Context content stays upstream, and codex-lb stores only ownership/participation plus authenticated encrypted result containers sent to the client.

## Maintainer review of 2026-09-08

- Rebase onto current main, including the timing seams from #2103, quota handling from #2125/#2078, Responses framing/contracts from #2168/#2114, and the migration from #2165. The actual parent is the subsequent current-main merge head `20260908_020000_merge_overflow_transport_heads`.
- Use the injected clock and scheduler for context deadlines and owned fan-out tasks. Cancellation must finish sibling cleanup before propagating.
- Pass parsed request metadata to dispatch bookkeeping. Ordinary unmarked requests do not activate context tracking; cached context sessions remain fenced even if a later request omits the marker. A marked request always validates durable ownership on a cache miss.
- Cache only committed positive ownership and participation, with a bounded process-local cache. Cache eviction/restart must never authorize a conflicting marked session; notes/history endpoints always check the database.
- Keep the initial durable ownership fence before upstream dispatch. For HTTP, record participation only after a classified event. A first-ever HTTP session therefore still needs one ownership commit and one participation commit. Holding a database transaction across network I/O or moving the ownership fence after dispatch would reintroduce earlier review bugs. Repeated dispatches to a recorded account should perform no context database work; an additional HTTP participant should require only its insertion.
- Consolidate the PR's six pre-archived folders here. Maintainers will archive the change at merge. The earlier commits retain historical review and test notes.

The cache is a process-local optimization, not a cross-replica discovery service. A session arriving without `reasoning.context=all_turns` is tracked only when already in this process's cache. Marked requests and explicit context operations recover their durable identity after restart or on another replica. Cross-replica discovery of unmarked sessions is not claimed.

References: https://github.com/Soju06/codex-lb/pull/2102#issuecomment-5584823927 and https://github.com/Soju06/codex-lb/pull/2102#issuecomment-5585728093.
