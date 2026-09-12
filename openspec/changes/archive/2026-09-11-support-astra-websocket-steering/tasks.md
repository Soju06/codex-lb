## 1. Protocol

- [x] 1.1 Validate and admit `response.steer` on an owned Astra WebSocket
- [x] 1.2 Bind automatic successors and explicit continuations to the parent
- [x] 1.3 Prepare explicit continuation before releasing the placeholder

## 2. Accounting

- [x] 2.1 Extend one successor reservation for additional queued steers
- [x] 2.2 Reduce only the unapplied increment on rejection; FOR UPDATE on that path
- [x] 2.3 Reconcile newly applicable limits for additional steering, including missing reservations and items; prove rejection, admission and exact terminal accounting through the socket boundary
- [x] 2.4 Defer sender cancellation until a newly committed reservation is attached to its cleanup owner; prove cancellation and concurrent first-steer rejection on real sockets for new and existing reservations

## 3. Verification

- [x] 3.1 Unit steering scenarios including late successor and prepare-before-swap
- [x] 3.2 Strict OpenSpec validation of this change
- [x] 3.3 Contain placeholder refund failures after explicit registration
- [x] 3.4 Consume anonymous terminals from suppressed late successors
- [x] 3.5 Infer apply_patch_call output for explicit continuations
- [x] 3.6 Sanitize upstream response.steer.failed before forwarding
- [x] 3.7 Revalidate and atomically swap explicit continuations after preparation, owner resolution and admission; prove reader rejection/assignment races and cleanup on the WebSocket surface
- [x] 3.8 Retain failed replaced-placeholder releases for bounded socket cleanup; prove transient recovery, accounting, unrelated progress, cancellation and owned teardown with deterministic WebSocket fault injection
- [x] 3.9 Retain final-steer release failures for tracked teardown retry, including cancellation and persistent failure
- [x] 3.10 Clear rejected unsent explicit replacements and prove a corrected continuation succeeds
- [x] 3.11 Retain rejected parent correlation through late created events, while preserving explicit and steering retry ownership; prove full WebSocket event sequences
- [x] 3.12 Suppress automatic successors arriving after explicit registration but before dispatch; prove placeholder-refund and account-cap await windows
- [x] 3.13 Prove both pre-write protection and legitimate post-write responses during flow-control waits using real transport and WebSocket route controls
- [x] 3.14 Refresh retained ORM reservation items after settlement/release claims; prove exact final charged usage
- [x] 3.15 Prove concurrent reservation adjustment and terminal claims with separate PostgreSQL transactions and exact final quota
- [x] 3.16 Replace send-duration suppression with a scoped transport-handoff notification across websockets, aiohttp, native egress and archiving
- [x] 3.17 Verify transport isolation, compression, cancellation and native acknowledgment ordering; run affected suites and independent review, then address its findings with focused regressions
- [x] 3.18 Preserve undispatched explicit ownership when a suppressed successor returns an ID-less terminal, including active/rejected steering and visible-request priority
- [x] 3.19 Bound historical steering correlation with admission stop and rotation after drain; prove required-tool-input progress, pending accounting, fresh-connection history and account-health neutrality
- [x] 3.20 Retire after timeout or local pre-send cleanup empties the queue, even without upstream events or keepalives; prove the next create uses a fresh connection
- [x] 3.21 Preserve canonical public auth, policy and quota classifications for steering while keeping unknown exception details private; prove refresh, policy and reservation paths with focused regressions
- [x] 3.22 Verify integration with the main-branch Clock/Scheduler and accepted-capacity replay changes, including virtual-time retirement after local cleanup with keepalives disabled
- [x] 3.23 Forward the normalized steering parent ID and prove echoed rejection releases the matching reservation before disconnect, preserving canonical IDs and string/structured input
- [x] 3.24 Normalize explicit required-tool parent IDs before continuation lookup; prove placeholder release before disconnect and completion before planned retirement for canonical and whitespace-padded IDs
- [x] 3.25 Discard completed Astra input and request/replay bodies before idle retention; preserve effective configuration updates and prove later steering, required-tool continuations and accounting through the WebSocket route
- [x] 3.26 Detach expired steering continuations through the existing bounded tombstone path; prove late-event suppression, current replacement ownership, explicit same-parent retry, rotation and exactly-once accounting
- [x] 3.27 Restrict steering snapshots to downstream WebSocket requests; verify HTTP bridge forwarding/completion and preserve direct steering configuration
- [x] 3.28 Reject new steers for a retired parent before admission or dispatch; prove delayed first acknowledgment and failure cannot claim a retry, while explicit creates and other parents retain their lifecycles and accounting
- [x] 3.29 Reset elapsed windows of newly applicable limits before steering reconciliation; prove admission against the fresh window on the WebSocket route
- [x] 3.30 Re-reserve an existing reservation item in the live window when its limit window advanced; prove the additional steer is admitted and settles once on the WebSocket route
