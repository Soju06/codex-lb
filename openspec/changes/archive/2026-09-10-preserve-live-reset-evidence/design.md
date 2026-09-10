## Context

See proposal.md for the missed trigger. Both writers persist UsageHistory. The scheduler currently evaluates warm-up only after a poll writes. The existing account/window/reset claim already provides durable consumption, including failed or pending attempts.

## Goals / Non-Goals

Recover current reset evidence through ordinary warm-up admission. Keep sender preflight, reset detection, plan selection and bootstrap freshness unchanged. General warm-up redesign and token-floor changes are outside this fix.

## Decisions

Read current snapshots and settings after every selected-account poll. Keep existing blocked-account reconciliation tied to successful polling, and retain its special anchored Free monthly recovery.

For opted-in accounts, find the earliest retained observation of the current reset identity, then search that span plus its immediate predecessor for a confirmed pair. Check the durable claim first to avoid rescanning consumed windows. The reset identity anchors the lookup across restarts and delayed deadlines; a moving duration cutoff would discard still-current evidence. No new database table or migration is needed.

Pass recovered pairs separately from current snapshots. The warm-up service uses the pair to prove the reset and identify the durable claim, while current snapshots govern availability. Initial-Free, paid-to-Free and staggered-idle paths remain gated on an actual poll write. A concurrent live snapshot cannot satisfy that gate merely by arriving after refresh starts.

An in-memory baseline would fail on restart or leader change. A separate event queue would duplicate the attempt ledger and require changes to both writers and schema. Retained history already contains the evidence needed for this bounded repair.

## Risks / Trade-offs

Busy unconsumed windows can contain many rows. Locating the first matching reset can scan retained history for the selected account/window; the subsequent read starts at that reset observation. Reads stop recurring after the durable claim. Retention that removes the predecessor leaves insufficient proof, so recovery fails closed.

Historical deadlines that drift beyond five seconds of the current identity are not replayed. Existing anchored monthly recovery remains unchanged. A claimed attempt is best effort; a crash after claim does not authorize a duplicate send.

## Migration Plan

Source-only rollout with existing schema and defaults. Reverting the code restores the previous scheduler behavior without changing stored claims or usage history. This task does not deploy the change.
