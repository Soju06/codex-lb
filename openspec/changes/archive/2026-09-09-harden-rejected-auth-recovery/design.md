## Context

See proposal.md for the review findings. Token rotation and access rejection
already use guarded database writes, but only rejection was conditioned on the
access credential. Routing caches maintain generation-fenced local marks.

## Goals / Non-Goals

Preserve account status authority and reject incomplete cross-account replay.
Do not change quota recovery, account ownership, schema, or operator settings.

## Decisions

- Keep the persisted deactivating refresh result instead of emitting a second,
  weaker authentication status from the retry layer.
- Reconcile only the exact access-rejection status with SQL CASE expressions in
  the existing token-rotation UPDATE. A separate post-write status update would
  recreate the race. Invalidate routing and selection after commit and adopt the
  fresh row in AuthManager so detached callers see authoritative status.
- Track reasoning that still needs a retained answer within each input turn.
  Require a completed non-commentary assistant answer with no pending tool calls,
  then apply the existing canonical call matcher and replay predicate. Recognized
  fields alone do not establish redundancy.
- Align overlapping canonical routing requirements with the existing distinction
  between refresh warnings and expired or proven-rejected access credentials.

## Risks / Trade-offs

- Conservative replay proof can reject partial transcripts. That is intentional;
  an authentication failure is preferable to silently dropping conversation state.
- Rotation touches shared authentication persistence. Deterministic tests cover
  both write orders, delayed cache marks, next selection, and preserved operator
  and quota state.

## Migration Plan

No schema migration or new setting. Merge after the focused regression and cloud
review gates; no deployment is performed as part of this review follow-up.
