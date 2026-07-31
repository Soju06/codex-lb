# Context: quota and bridge lifecycle cleanup

## Purpose and scope

This change closes the short ownership gap between API-key quota admission and
route-specific settlement ownership. Normative behavior lives in
[`specs/api-keys/spec.md`](./specs/api-keys/spec.md).

It also closes two HTTP bridge lifecycle gaps found while reviewing the bridge
regressions: competing cleanup paths could both close one session, and
terminal-reset or shutdown work could lose its cleanup fallback after the
session left the local registry. Normative bridge behavior lives in
[`specs/responses-api-compat/spec.md`](./specs/responses-api-compat/spec.md).

## Decisions and constraints

Header calculation remains after admission so successful responses continue to
reflect the committed reservation. The route keeps cleanup ownership only
until headers are ready; stream or service settlement then continues unchanged.
Borrowed reservations remain owned by their origin.

HTTP bridge cleanup claims one session-level close owner before any
post-detachment await. Failed registration, scheduled cleanup, upstream-reader
retirement, local terminal reset, and shutdown share that claim. The owner runs
close through the existing bounded, tracked cleanup path so a timeout does not
silently abandon resource release.

Local terminal reset installs its close fallback before pending-request cleanup
can fail or be cancelled. Shutdown first detaches and claims every registered
session, then closes owned sessions sequentially, drains tracked close work in
a `finally` path, and defers caller cancellation until that cleanup finishes.
An individual close failure is logged and does not skip later owned sessions.

## Failure modes

A database, cache, or calculation error while building rate-limit headers can
occur after quota has been reserved but before upstream work begins. The owned
reservation must be released once before that error propagates. A separate
failure of the release persistence itself is logged without replacing the
header error and continues to use the repository's existing stale-recovery
contract.

For an HTTP bridge session, cleanup may race between the upstream reader and a
direct, scheduled, reset, registration-failure, or shutdown path. Only the
successful owner claim may initiate close. Once a session is detached, failure
or cancellation during pending-request or shutdown bookkeeping must not leave
it without bounded close work that shutdown can drain.

The earlier CI-only `bridge_instance_mismatch` failure was not reproduced and
is not attributed to these lifecycle gaps. This change specifies and tests only
the directly demonstrated exact-once and cleanup-fallback failures.

## Concrete example

For a limited `POST /v1/responses` request with `stream: false`, admission first
commits reservation `R`. If rate-limit header construction then raises, the
route releases `R` exactly once, starts no upstream stream, and preserves the
header failure for normal error handling.

For a bridge shutdown with sessions `A` and `B`, shutdown removes both from
local reuse and claims both before awaiting waiter notification. If the caller
is then cancelled while `A` is closing, bounded cleanup still processes `A`
and `B`, drains any tracked close task, and only then re-raises cancellation.
