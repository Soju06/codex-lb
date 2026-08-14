# Deduplicate HTTP bridge retry-circuit failures by send attempt

## Summary

An eventless HTTP bridge `response.create` can be observed by both the upstream
reader watchdog and the downstream stream-idle watchdog. Each observer currently
persists an independent retry-circuit failure even though both are reporting the
same upstream send. With the default threshold of two, one silent send can
therefore open the circuit and make a later request fail locally with HTTP 503.

Track the individual process-local upstream send attempt and let all failure
observers claim that attempt through the existing retry-circuit lock. The first
eligible observer records and persists the failure; later observers of the same
attempt reuse the resulting count without incrementing or persisting again.

## Why

The retry circuit is intended to protect a hard-affinity key after repeated
failures. A single upstream send observed through two local timeout paths is one
failure, not two. Durable conflict merging deliberately treats independent
persistence calls as independent failures, so deduplication must happen before
the durable write.

A time-window or session-key dedupe would hide legitimate retries. A single
"last generation" marker is also insufficient because an observer for an older
send may resume after a newer send has started. A stable object per send keeps
old and new attempts distinct without adding a durable identifier or an
unbounded process-level set.

## What Changes

- Create a process-local attempt object immediately before every HTTP bridge
  upstream `response.create` send.
- Disarm that attempt when the send fails or is cancelled, and mark it observed
  when a matching upstream response lifecycle event wins the race.
- Capture the attempt at the moment a watchdog classifies a timeout, before any
  recovery, reconnect, or cleanup await can install a newer attempt.
- Pass the captured attempt through all retry-circuit failure funnels.
- Atomically claim the attempt and increment the circuit under the existing
  retry-circuit lock; only the first claim performs durable persistence.
- Emit low-cardinality observability when a duplicate observer is suppressed.

## Impact

- One eventless send contributes at most one consecutive retry-circuit failure.
- A separately dispatched retry or replay remains a distinct eligible failure
  and can open the circuit as the second strike.
- Existing circuit thresholds, cooldowns, error envelopes, account-health
  handling, continuity guards, and durable conflict merging remain unchanged.
- No schema migration, runtime setting, or operator action is required.

## Non-Goals

- Determining or eliminating the upstream cause of an eventless send.
- Changing the eventless timeout, stream-idle timeout, retry threshold, or
  cooldown durations.
- Adding cross-replica send-attempt identifiers. Only the active bridge owner
  owns the upstream socket; a send after owner handoff is a new physical attempt.
- Changing replay eligibility, account selection, or continuity fail-closed
  behavior.
