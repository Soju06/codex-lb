# Design

## Context

ApiList owns local search/status state; ApisPage owns selection and actions. The list response already provides lifetime usage and lastUsedAt; lastUsedAt can lag the server's write-behind flush. No new API is required.

## Decisions

- Keep one ApiList mounted in both layouts so search/filter/sort/page state survives switching. Persist only view mode using the same storage-failure handling as Accounts.
- Reuse pooled and limit presentation in sidebar and overview rows. Preserve the visibility rules for 5h capacity and unknown weekly usage.
- Reuse ApiDetail in the inline panel or List dialog, and enable trend/7-day queries only while details are visible.
- Define unused as absence of both last-use timestamp and positive lifetime request count. This protects keys whose request history was pruned and keys whose last-used write is delayed. It represents the current recorded snapshot, not a guarantee about in-flight requests.
- Sort raw summary fields, keep unknowns last in either direction, then slice 24 rows. Status order is Active, Disabled, Expired; a disabled key keeps its Disabled display status.

## Risks / Trade-offs

- Long names and dates can widen phone rows: truncate identity with a title, stack metadata and verify 320px/browser interactions.
- Dialog actions can open nested dialogs: use existing dialog primitives and verify edit/enable/disable behavior through the page path.
- The existing edit dialog can exceed phone height when its two columns stack. Constrain its outer height and allow scrolling so Close remains reachable when opened from the List dialog.
- Overview aggregates remain fleet-wide to preserve their existing lifetime semantics.
