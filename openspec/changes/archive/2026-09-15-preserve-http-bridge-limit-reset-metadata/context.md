The retry branches previously constructed a message-only error. The terminal
path skipped a second health write because the retry branch had marked health
as handled, so the original reset evidence never reached persistence.

For example, a five-day reset could become the normal 30-second fallback.
Owner retirement then saw a deadline inside the HTTP request's two-hour budget
and retained the owner. Preserving the upstream error fixes that decision at
its evidence source; it does not promote advisory usage snapshots to blocking
state or relax full-resend proof checks.

Operational evidence must distinguish reproductions from historical inference:
the observed deployment used short cooldowns and retained an exhausted owner,
but its original upstream reset frame was not retained. A regression proves
the metadata-loss defect independently of that missing historical frame.

Validation: the regression failed before the change with a stored reset of
`2000000030` instead of `2000432000`. Both HTTP endpoints and both reset fields
pass after the change. The HTTP bridge/owner-recovery suite passed 239 tests;
the bridge and proxy unit suites passed 2,475 tests. Final parser/proxy coverage
passed 1,449 tests, including rejection of boolean reset metadata. `make lint
typecheck` and independent review passed. Full `local-ci` passed frontend
checks (1,627 tests) and Rust formatting, lint, tests, and build, then stopped
because the local machine lacks `cargo-deny`; its later targets did not run.

A separate check of PR #2439 at `d4a3280d552a80a4ba6623e4e4a9e0350fabfa3c`
confirmed the distinction between client-terminal metadata and account-health
metadata: adding only the deferred-health assertion still failed with both
reset fields absent. This change complements that terminal-rendering work.

A code-only beta.9 backport was also verified in a deployment: the exhausted
owner's reset persisted for five days, the next request logged
`owner_retired_on_request` / `rebind_without_anchor`, and the same durable
session completed subsequent operations on a different account. HTTP and
WebSocket smoke requests completed through both loopback and the public route.
The database container and schema revision were unchanged.
