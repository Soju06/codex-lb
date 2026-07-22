## 1. Task ownership and drains

- [x] 1.1 Add a typed, deadline-bounded task-set drain primitive that rechecks after completion callbacks run.
- [x] 1.2 Track every asynchronous audit-log write, consume its result, and expose an audit-specific shutdown drain.
- [x] 1.3 Expose a fleet refresh shutdown drain over the existing cancelled-request task registry.
- [x] 1.4 Drain audit and fleet tasks concurrently in the application lifespan before usage singleflight, HTTP, and database teardown, isolating drain failures.

## 2. Regression coverage

- [x] 2.1 Cover task-set completion, callback recheck, and timeout behavior.
- [x] 2.2 Cover audit fire-and-forget ownership, successful drain, unexpected failure cleanup, and timeout reporting.
- [x] 2.3 Cover cancelled-request fleet refresh draining, timeout reporting, and task cleanup.
- [x] 2.4 Cover lifecycle ordering and prove one failing drain does not skip the other.

## 3. Verification

- [x] 3.1 Run focused unit/integration tests plus Ruff format/check and ty for changed Python files.
- [x] 3.2 Run strict OpenSpec validation, architecture/simplicity gates, and relevant broader test suites.
- [x] 3.3 Perform an adversarial lifecycle review and reconcile every requirement/scenario with implementation evidence.

## 4. P1 review follow-up: post-timeout admission race

- [x] 4.1 Close a synchronous audit/fleet task-admission barrier immediately after in-flight draining, keep it closed through resource teardown, and own every fleet refresh task from creation.
- [x] 4.2 Cover post-cutoff audit rejection, fleet ownership before caller cancellation, the fleet route's 503 contract, and the real late-cancellation lifespan race through in-flight HTTP middleware.
- [x] 4.3 Re-run focused and broader tests, static checks, strict OpenSpec validation, architecture/simplicity gates, and an adversarial standalone-diff review.
