# Context

## Purpose and boundary

The retry circuit protects a hard bridge key after repeated eventless upstream
failures. It has two distinct state planes:

1. The durable row carries failure count, last detail, version, and a wall-clock
   cooldown deadline. Those fields merge across replicas and are the shared
   protection against repeated upstream failures.
2. The in-memory state carries the monotonic deadline and the active half-open
   lease. The upstream websocket and its pending request objects live in one
   process, so lease ownership is process-local and is bound to that session.

Keeping those planes explicit avoids pretending that an in-memory lease can be
recovered or fenced cluster-wide without a schema/API change. A replica that
loads an already elapsed durable row may admit its own local probe; it still
honors any real future durable cooldown and any durable failure updates.

## Rationale

The durable row cannot identify the process-local websocket or request that
owns an in-flight probe. Reusing that row as a distributed lease would add a
schema and coordination contract that this focused change does not need.
Instead, replicas share durable cooldown and failure state while each process
fences its own probe with the concrete session and request-state identities it
already owns.

The reset path takes lifecycle ownership only for the non-blocking state
transition. Pending-request settlement and transport close remain outside that
lock, but inside cancellation-shielded cleanup, so a slow close cannot block a
new submitter while cancellation also cannot strand the detached session.

An active probe is not reclaimed merely because the default 600-second local
lease elapsed. If its owning request is still pending after an attempted
`response.create` and before terminal settlement, the lease is renewed through
that request's `bridge_request_deadline`. Completion settlement carries both
the durable episode observed at admission and the process-local lease
generation, so a late completion cannot settle a replacement probe that reused
the same hard key.

Detached cleanup also retains failed account-lease handles after transport
close. The account-scoped cleanup pass retries those handles, and concurrent
retries share one in-flight close/release task. The detached registry is
cleared only after all retained releases succeed, so a transient balancer
failure cannot silently strand account capacity.

## Issue and vehicle map

This change is intentionally split from the neighboring vehicles. Exact branch
heads and merge-base belong in the PR's current-status evidence rather than in
stable capability context:

- #1908 contains the accepted
  elapsed/absent-row arithmetic root cause, but its equal/newer lease-clearing
  behavior is superseded here because it breaks #1394 single-flight.
- #1947 is the focused vehicle for
  #1943 cooldown-created undispatched WebSocket session freshness/retirement;
  it does not own this retry-circuit arithmetic or probe-return contract.
- #1857's accepted semantic source is limited to commits
  `d007582968d0c9b41ed29a6002226bbd63d07313`,
  `9a7dc342148cf471b13a9980d411ea96d654e19e`, and
  `2a822b4f9b522d4972e12a357d019a033b900805`: owner-tracked release, cancellation-safe
  teardown, and replica-boundary reasoning. Its broad/relanded branch is not
  carried as a vehicle.
- #1891 owns poisoned-anchor quarantine and episode/generation-proven
  replacement invalidation; this change preserves equal/unchanged elapsed
  snapshots and does not import quarantine.
- #1867 remains the broad stale-anchor hardening vehicle; no migration,
  attribution, or broad anchor changes belong here.
- #1902 is the sole attribution carrier. This change does not edit contributor
  files or recreate closed #1951.

The successor PR must cite maintainer comments #1908 `5423573461` and #1857
`5423566424`, and the issue-separation statement in #1943, alongside this map.

## Failure modes and controls

The September 7 review reproduced two timing gaps. Reader-origin owner loss
spawned cleanup which cancelled and awaited the reader waiting for that same
cleanup. The caller identity must be removed from the reader slot before
spawning cleanup, while a foreign reader remains owned and is cancelled.
Separately, equal monotonic timestamps cannot order durable loads and probe
returns. A logical pending-return marker must survive same-tick reconciliation
and be consumed by the next admission or a fresh authoritative reset.
An in-flight result cannot erase the later return, but its stronger future
cooldown still suppresses replacement admission. Discarding that whole result
would let the returned probe bypass newly observed durable protection.
An applied reset is a later ordering boundary of its own. Results started
before that reset cannot merge the ended episode back into local state,
including when return, reset and reconciliation share a clock tick.

- A stale session cannot return a newer session's probe because the release
  checks the owning session identity.
- A continuity-owner failure does not increment `consecutive_failures` or
  persist a new row. An actual `stream_incomplete`, `stream_idle_timeout`, or
  `clean_close` still increments and opens the circuit at the configured
  threshold. Existing anchor replay and error-provenance rules remain outside
  this change.
- Disarming before the first await prevents the reader from classifying reset
  teardown as an eligible eventless send. Acquiring `lifecycle_lock` closes the
  submit-vs-reset gap in which a late submit could append an undisarmed attempt.
- If durable lookup fails, the existing local state remains authoritative for
  the process. The release is still best-effort and fenced by the active local
  owner; no durable clear is invented for a process-local lease.
- If account-lease release fails after transport close, the failed handle stays
  on the detached session and is retried by explicit account cleanup. A second
  concurrent retry joins the existing task rather than issuing a duplicate
  release.

## Example

Two failures open a hard key for 60 seconds. After the deadline, local session
A is the only admitted probe. The proxy loses continuity ownership; its reset
detaches A, disarms its pending attempt, and returns the lease as an elapsed
cooldown. A concurrent reconnect on the same process is suppressed until the
next request acquires a fresh probe. A different replica may admit a local
probe after loading the same elapsed durable row, but both replicas still honor
a future durable cooldown and merge genuine failures through the durable row.

## Upstream timing integration

The upstream clock and scheduler collaborators also own the PR's added cleanup
tasks. Cancellation deferral still spans admission handback, probe return, and
eligible retirement; changing the task-spawn primitive does not change that
ownership or the original typed-error precedence. Retained account-lease retries
use the same scheduler while preserving single-flight close ownership and the
guard against closing an unrelated live session.

Virtual-time regressions drive long-lived probe expiry and durable reloads with
the service clock. Admission cancellation and retained-lease retry tests cover
both real defaults and a recording virtual scheduler, asserting that cleanup
tasks finish with no owned task or timer left behind.

## Upstream bounded sweep integration

The bounded detached-retirement sweep applies its five-second timeout only to
each detached session's pending-lock acquisition. Mandatory lifecycle owners
still wait without that bound. A timed-out pass must preserve retained account
leases and detached tracking; a later sweep can share the existing close/retry
owner with account cleanup. Acquiring the lock does not override admission
waiters or foreign handoff reservations. The bound is not a whole-sweep or
whole-close deadline.

The integration preserves upstream's AnyIO floor and locked 4.15.1 runtime,
alongside its API and keepalive cancellation-cascade fixes. Those outer task
boundaries complement the PR's deferred admission/probe cleanup; they do not
replace its typed-error precedence or ownership fences.

## Persisted exhausted-owner selection

Evidence-gated quota recovery can leave a required owner unavailable after a
fresh exhausted usage sample. The real owner-restricted selector reports
`continuity_owner_unavailable`, not the ordinary pool's `usage_limit_reached`,
and its message may still contain a retry hint. That explicit owner failure
enters the existing ordered probe-return cleanup before the generic hint parser.
This changes neither hard-affinity saturation waits nor local-capacity recovery.

Ordinary selection failure is not continuity loss. Without a required owner,
reconnect completes its failed handoff and leaves normal reader retirement to
emit the selection error. Running owner-loss cleanup first would settle the
downstream queue before the later ordinary error override can take effect.

## Upstream accepted replay integration

Accepted output-free replay now shares the internal retry path with half-open
probe admission. The request may already have one send attempt when the retry
claims its probe. Undispatched handback therefore compares against the retry's
send-attempt baseline, not zero. If admission's durable lookup yields while the
pending request is replaced, the admitted request identity must still match
before the retry claims the session create gate.

The upstream replay contract owns lifecycle identity, duplicate-prelude
suppression, gate reclamation, and account/anchor safety. This change retains
those rules together with its probe and cleanup ownership. In particular, a
failed replay must not strand a replacement account's lease on an already
closed original transport.

Admission already extends a newly claimed probe through the original request
deadline. A staged accepted terminal can retain its abort-settlement claim
while reconnect waits without shortening that lease to the default window.
After the request budget expires, retry admission checks prevent another send;
late handback must leave any replacement probe untouched.

## Validation note

With pinned `@fission-ai/openspec@1.11.0`, strict validation passes for this
change and the synchronized Responses spec. The repository's canonical
`validate --specs` gate passes all 63 specs after reconciling upstream
`c38e4de15beead64910cf074e9e457781aeb5ff7`. Adding `--strict` reports 22 failures
in other, unchanged specs; it is not an all-repository strict pass.

The upstream reconciliation preserves both this change's probe scenarios and
the synchronized cooldown-suppressed session-retirement scenarios. Existing
terminal tests cover both text-decoded and native-interpreted events, and
single-flight probe admission remains independent of the dashboard-managed
upstream account-breaker toggle. The sibling eventual-success settlement and
expiry policy questions remain unresolved; this integration does not change
their behavior.

The later reconciliation with upstream
`0da41b64e802331faae01077c89e8c8580b4bce0` passes strict validation for all
64 main specs with the same pinned validator. Upstream has repaired the
unrelated strict-validation failures recorded above. The accepted HTTP
continuation promotion uses soft, API-key-scoped locality; it does not grant
hard-continuity replay or probe ownership. Chat reservation settlement remains
with the actor that accepted it. Automation run claims use their upstream
captured-budget contract, independently of the unresolved bridge-expiry
policy. This reconciliation preserves those contracts without changing the
sibling-success or probe-expiry decisions.


## Independent classifier extraction on current main

The 2026-09-09 reconciliation includes upstream `c0beaaadd96a89f0240582b5449bf4dd50647c7d`, including its fixed bridge constants. It preserves the original contribution history and Lucas Klein's HTTP half-open regression and Tests credit.

Reason-only `response.incomplete` classification now belongs to the independent `count-http-bridge-incomplete-reason` change and issue #2273. This probe change uses the existing explicit-error classification for its delayed terminal ownership regression, so its tests do not require the other branch. Its circuit eligibility and continuity-provenance requirements remain intact. The stuck-gate reader-cleanup test uses upstream's current constant rather than the removed setting.

Both branches start from the same current main. The reduced probe branch passes 103 focused cases on its own; the classifier passes 19 cases on its own. Applying them in either order produces the same tree, and the combined 122-case set passes. These local results do not resolve the sibling-success settlement or abandoned-probe expiry questions tracked in #2272, and do not establish hosted approval or merge readiness.
