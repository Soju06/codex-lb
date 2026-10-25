## Context and decisions
Use an owner-local set of tasks and completion callbacks, matching existing asyncio task ownership patterns. No global task manager or new background worker is necessary. Refresh completion already consumes its own failures without request-local credential context; retain that behavior. OAuth idle-stop callbacks remove finished references and retrieve exceptions with a fixed content-free diagnostic.

## Scope and constraints
The callbacks remain asynchronous: a successful OAuth response need not wait for listener shutdown. Refresh waiters retain existing cancellation independence, successor guards and negative-cache semantics. This does not add a new shutdown grace, retry loop or token policy. Tasks that are still pending retain their references; completion/cancellation releases them.

## Verification
Hold cleanup at a deterministic event/lock boundary, observe ownership while pending, then release the boundary and verify completion and reference removal. Cover success, failure and cancellation, including the refresh successor guard and existing callback route tests. No synthetic forced-GC test is presented as evidence of a production garbage-collection failure.
