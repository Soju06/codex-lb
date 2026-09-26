## 1. Regression evidence

- [x] 1.1 Convert the twelve isolated review probes into maintained route regressions; verify all seven original failure groups reproduce before fixes and preserve the existing dirty-tree baseline.

## 2. Request compatibility and routing

- [x] 2.1 Support declared namespace tools and neutral generation controls in source pools while retaining safe replay rules; verify single/multi-source behavior on canonical/native routes, streaming/JSON and trailing slashes.
- [x] 2.2 Resolve known source ownership when no matching candidate remains; verify removed sources and disabled streaming fail closed without subscription dispatch, while file/subscription precedence tests still pass.
- [x] 2.3 Validate ownership of the effective payload after overrides; verify conflicting anchors/input are rejected before any upstream call and ordinary overrides still work.
- [x] 2.4 Preserve ownership across identical upstream token updates; verify same-token continuation and changed-token rejection on another application instance.
- [x] 2.5 Publish and resolve unresolved tool call references; verify mixed-owner call_id-only outputs fail before dispatch and complete portable call/result pairs remain supported.

## 3. Historical and expired ownership

- [x] 3.1 Document and implement the smallest durable historical/version fence; verify legacy collision publication cannot displace a retained owner and concurrent PostgreSQL claims remain atomic.
- [x] 3.2 Prevent expired/pruned ownership from authorizing replacement credentials; verify both retention states, known historical compatibility, ambiguous evidence denial, and retention concurrency.
- [x] 3.3 If schema changes are needed, add an additive migration on the intended sole head with explicit historical data handling; verify CLI upgrade/check, downgrade/upgrade, row preservation and schema drift on SQLite/PostgreSQL, or record why no migration is needed.

## 4. Integration and independent review

- [x] 4.1 Run scoped lint/type/format, timing/cancellation/architecture guards, route/alias/dispatch/forwarding regressions and source pool tests; record commands/results and verify no unrelated edits.
- [x] 4.2 Exercise shared PostgreSQL with separate application instances/sessions, competing ownership publication, retention and cancellation/partial-failure cleanup; verify reservations settle and admission slots/tasks are released.
- [x] 4.3 Run an independent adversarial review of the final diff, resolve confirmed findings with regression evidence and record any limitations; do not claim readiness from old passing suites.
- [x] 4.4 Sync normative specs and stable context, validate OpenSpec strictly, write verification evidence and archive only when complete; verify documentation matches mixed-version HA behavior and production remains unchanged.
