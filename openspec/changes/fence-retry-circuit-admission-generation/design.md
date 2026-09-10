# Design: retry-circuit generation fencing

## Boundary

The durable `http_bridge_retry_circuits.admission_generation` integer is the
linearization point for one internally authorized stale-anchor replay. The
existing `updated_at_epoch`, failure count, and cooldown remain the observed
failure state. They are compared in the claim so a stale snapshot cannot win,
but they are not used as the admission version because wall clocks can be
skewed and delayed failure writes must still merge.

Each successful claim also records its generation, start epoch, and expiry in
nullable columns. The default lease is the two-hour HTTP bridge request budget
plus 60 seconds of cleanup grace. A request with a shorter configured budget
passes that relative remaining budget plus the same grace. The repository
derives the stored expiry and all active/expired comparisons from the database
statement clock; replica wall-clock values never decide lease ownership. The
claim-start epoch remains an identity fence for timeout reconciliation, not a
liveness clock. The marker is active only until its expiry; legacy rows with a
null expiry are reclaimable by the normal generation CAS. The migration leaves
all three columns nullable so existing rows remain valid. Its downgrade checks
for an unexpired receipt against the same database clock and
refuses before DDL or Alembic version stamping when one exists; once no live
receipt remains, it drops the marker columns so the parent revision's ORM
schema does not trip startup drift checks. The check and drop run under one
critical section: PostgreSQL uses `LOCK TABLE ... IN ACCESS EXCLUSIVE MODE`,
while SQLite uses `BEGIN IMMEDIATE` to take the writer slot. A direct Alembic
rollback therefore cannot race a durable claim that does not use the broader
migration lock.

The unmerged marker migration follows
`20260909_070000_automation_run_claim_budget`, after report rollups, dashboard
routing/overload, resilience and timeout revisions and the overflow/transport
repair from #2198.
That merge joins the subscription-overflow and transport-sentinel revisions
without changing either. The marker's operations affect only the retry-circuit
table; upgrading or rolling it back must preserve the upstream schemas,
overflow settings, explicit transport choices, model-source pins, dashboard
resilience, timeout, routing and overload overrides, report history and fold
watermarks, captured automation claim budgets and legacy NULL budgets, and
retry generations.
All merged revisions remain unchanged.
This graph correction does not choose the stranded-receipt policy or add
late-receipt recovery.

The upstream merge regression tests the historical merge boundary explicitly.
Separate populated upgrades go directly to the current head and compare against
current ORM metadata. A later child revision must not make that historical
no-op merge appear to add the child's columns or fail current-head assertions.
The direct-to-head cases also verify that new nullable dashboard columns start
NULL. The marker lifecycle checks the report watermark's epoch default, then
seeds a progressed watermark and populated report measures alongside explicit
dashboard overrides at its latest parent. It compares the preexisting schema
and data through upgrade, rollback refusal, writer exclusion, safe rollback
and re-upgrade. Rolling back only the marker never drops report history, which
may be the only remaining copy after raw-log retention.
The same lifecycle preserves automation run rows with captured and NULL
budgets; marker rollback never traverses the automation budget migration.

HTTP continuation promotion broadens entry to this bridge but does not change
replay authority. Inferred history and conversation locality remain soft and
API-key-scoped; hard turn/session ownership and generation-fenced admission
still govern their existing paths. Conversation payloads retain their own
identifier without an injected previous-response anchor. Promoted Chat requests
keep the API's reservation ownership until the existing service-cleanup-ready
or owner-forward signals transfer it. Reconciliation tests these interfaces
without selecting a stranded-receipt or deferred-settlement policy.

## Claim path

At stale-anchor authorization, the bridge captures an immutable
`_HTTPBridgeRetryCircuitGeneration` containing the durable fields and the
process-local failure/cooldown state. Immediately before queue publication,
the submitter checks the local state under the retry-circuit lock, releases the
lock for durable I/O, and issues one conditional insert/update. SQLite and
PostgreSQL are the only supported durable dialects for this path; the
statement uses `RETURNING` so a successful receipt is returned by the same
atomic write rather than a second read that could time out after consuming the
generation. A missing row is created only when the captured generation proves
absence (`generation == 0`). The conditional update may reclaim an expired
marker, but it must reject an active marker and always writes a new expiry
alongside the incremented generation.

If the first bounded claim times out, cancellation does not prove whether the
database committed. One identical compare-and-set may be retried within the
remaining request deadline when the first operation has settled cancellation.
A cancellation-resistant first operation is detached and no concurrent retry
is issued; its eventual commit is therefore fail-closed for this request. A
committed first claim rejects a settled reconciliation through the incremented
generation; an uncommitted one can be recovered. If that settled
reconciliation refuses, one bounded receipt lookup may adopt the claim only
when the durable generation and claim start exactly match the receipt captured
by this request and the database clock confirms its stored expiry is live. A
lookup timeout or error remains undecided; an absent, expired, or mismatched
receipt is a confirmed competing claim. Refusal, store errors, a second
timeout, or an expired deadline otherwise remain fail-closed.
After a successful receipt, the local state is checked again under the lock;
any intervening same-key failure or cooldown suppresses the replay.

## Integration with the proxy clock and scheduler

The retry claim, diagnostic lookup, release retry, and shutdown drain paths
use the owning service's clock and scheduler from `app/core/clock.py`.
Production retains the real adapters. Virtual-time tests can therefore
advance a caller deadline and observe every spawned claim/release task.
Database statement time still decides durable receipt expiry and reclaim.

The bounded cancellation drain remains four event-loop yields. A settled
claim timeout permits one reconciliation only within the original caller
budget; an operation still draining cannot overlap a new claim. For example,
a budget lasting one attempt returns undecided at its first timeout, while a
budget lasting two attempts permits one more bounded CAS.

Upstream gate-release scheduler arguments are retained inside the
failure-isolated submit cleanup. Account cleanup exceptions and cancellation
still trigger the generation-fenced release, and the original error survives
that bounded attempt. No lease duration or abandonment policy changes here.

## Open decision: stranded claim receipt lockout

The receipt is intentionally retained when a request leaves the pending set
without proving that its replay was dispatched, or when durable release cannot
be confirmed. That preserves the generation fence, but a stranded receipt can
block the same-key replay until its lease expires (the default lease is 7,260
seconds: the two-hour request budget plus 60 seconds of cleanup grace). The
current fail-closed response reports the normal minimum `Retry-After: 1`, so it
does not expose that longer lockout window.

This change does not choose a product policy for that trade-off. Maintainer
sign-off is required on exactly one reversible direction before this change is
delivery-complete:

1. Shorten the abandonment lease, for example to the 600-second half-open
   lease, accepting earlier reclaim of an owner that may still be finishing.
2. Add an explicit reclaim owner and handoff protocol for receipts orphaned
   after the request leaves `pending_requests`.
3. Keep the lease and make `Retry-After` lease-aware, documenting the accepted
   lockout window to clients and operators.

Until that decision is accepted, the implementation keeps the existing
fail-closed lease and does not pretend that a one-second retry hint is an
accurate timer for a stranded receipt.

## Reset path

Successful response settlement first loads the durable row. A failed lookup
does not establish a version fence, so it leaves the local state and marker
sets intact. A confirmed miss may remove only a local marker that has not been
updated since the lookup began. A present row is cleared by a conditional
update matching both its observed timestamp and admission generation. A false
row-count result means a newer durable writer won and the local state is kept.
A same-key success may reset the failure observation while an active replay
receipt remains in place; the receipt is cleared only by its owning replay's
generation-matched release.
Only after a successful CAS does the local state get removed, and a local
failure that arrived after the lookup still wins the post-CAS check.

Pre-dispatch submit cleanup captures the response-create attempt count when it
installs a claim and may release that claim only if the count is unchanged.
The release is in a failure-isolated `finally` around account/gate cleanup and
defers caller cancellation while its bounded durable attempt settles. This
preserves ownership when a send was ambiguous but produced no operation ID.
When terminal stale-anchor handling resets a session for a same-owner
retry, it detaches the receipt before reset and transfers the claim key, lease,
and attempt fence to the retry request state so reset cleanup cannot release
the retry's marker. The retry attempt fence is initialized from that fresh
state's current response-create attempt count; the source count is used only
while restoring the detached receipt if recovery setup fails.

Expired-row purges compare both the observed timestamp and
``admission_generation`` and only delete rows with no active claim lease. The
per-key loader and the batch cleanup scheduler carry the generation and claim
receipt selected by their read into the delete predicate, so a claim that
advances the generation cannot be deleted by a stale purge and later recreated
from generation zero. A terminal request releases its marker by matching the
claimed generation and optional timestamps; a release from an older owner
cannot clear a reclaimed marker. If that conditional delete returns no match
or cannot complete, the loader reports an uncertain stale purge to its caller
before any reload; a refreshed below-threshold row may update the cache but
cannot authorize this call. Pre-created admission remains fail-closed rather
than using an untrusted local fallback. The result is carried per call so
concurrent loads cannot clear each other's uncertainty.

## Delayed failure merge

Failure persistence continues to merge by the existing base observation
timestamp and failure-count rules. It never rewrites `admission_generation`.
Thus a failure write delayed by a skewed wall clock can merge after a replay
claim while the independent integer continues to fence a later claim.

## Proof seams

- Unit tests cover immutable snapshot construction, local pre/post checks,
  unrelated-key concurrency, timeout reconciliation, and marker cleanup.
- Durable integration tests cover SQLite `RETURNING` claims, stale-generation
  refusal, active-lease purge blocking, expired-marker reclaim, absent-row
  races, generation/receipt-fenced clears, migration upgrade/downgrade, and
  delayed failures.
- The call-site regression passes the original bridge request deadline into the
  claim; no retry can spend an unbounded second claim timeout.


## Independent purge extraction

Issue #2270 owns the generic scheduled-purge snapshot race, using columns already on main. The receipt candidate retains active-receipt exclusion and matches the selected nullable receipt generation/start/expiry before deletion. It keeps main's per-row delete structure, with a small fixed bind count; no tuple batching is needed. A receipt mismatch ends the selected pass. Per-key generation fences remain unchanged.

The generic lagging-clock and timestamp batch regressions move with #2270. The prior exact two-DELETE assertion described tuple batching and is replaced by the unchanged externally observable retention and bind-limit coverage. No accepted claim lifecycle behavior or held policy is removed. Both merge orders must preserve all existing-column and receipt predicates. Both candidates remain independently testable on current main.

## Mixed-version activation remains unresolved

An additive marker migration does not fence receipt-unaware replicas during a
rolling update. Current upstream's claim implementation can load generation 1
with a live receipt, advance it to 2, and admit another replay while the marker
still belongs to generation 1. A dedicated SQLite diagnostic using the exact
upstream `efe0f581a` repository method reproduces that transition against this
candidate's schema. This is repository-level proof, not a two-replica traffic
trial.

The release needs an accepted activation contract, such as delaying activation
until every serving replica understands receipts or requiring a non-rolling
cutover. Neither has been selected. Schema compatibility and guarded downgrade
proof do not establish mixed-version admission safety; task 4.23 remains open.
