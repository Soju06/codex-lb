## Context

The WebSocket connect loop already distinguishes a hard continuity/file owner
from a preferred account. A pre-created terminal-event replay currently makes
that distinction from `preferred_account_id` alone and preserves the rejected
envelope only for the legacy entitlement alias. See `proposal.md` for the
behavioral motivation and the delta specs for the contract.

## Goals / Non-Goals

**Goals:**

- Reuse one required-owner predicate at connect and pre-created replay points.
- Retain the exact upstream model-rejection envelope until a replacement
  connection succeeds or the bounded replacement selection is exhausted.

**Non-Goals:**

- Do not alter retry budgets, health accounting, accepted-response replay,
  routing policy, or the separate overload-backoff carrier.
- Do not add settings, persistence, or a new retry mechanism.

## Decisions

1. Extract the existing four-signal required-owner predicate into the WebSocket
   helper layer and use it at both decision sites. A non-null preferred account
   is not sufficient because forced refresh uses it as temporary routing state.
   Duplicating the expression would let the two sites drift again.

2. Generalize the existing pre-created model-rejection fallback state instead
   of creating parallel envelope storage. The state already retains status,
   code, message, type, and param for the legacy entitlement rejection; exact
   `model_not_found` uses the same bounded lifecycle. It is cleared when a
   replacement connection starts, so a replacement's own failure is not hidden.

3. Decide pre-created replay ownership after the replay body is prepared,
   mirroring the accepted-replay branch. The anchored follow-up of a Codex
   session is pinned to the anchor's owner at dispatch, and only the fresh-body
   prep releases that pin together with the proxy-injected anchor. Evaluating
   the owner predicate before the prep silently disabled the existing legacy
   entitlement failover on every continuation turn. The owner that survives
   the prep is one the connect hard-requires (turn state); re-sending the same
   model there can only be rejected again, so that owner keeps its original
   envelope without a replacement connect.

4. Hold the HTTP lifecycle prelude instead of rewriting it. A fresh,
   replay-eligible direct HTTP stream keeps `response.created` and
   `response.in_progress` in a bounded buffer (one frame per type) until the
   first output token, a terminal event, or EOF; SSE comments pass through.
   An output-free overload terminal then re-enters the existing
   `_RetryableStreamError(exclude_account=True)` path, so the client sees
   exactly one lifecycle, from the account that ran the turn. Emitting
   `response.created` immediately and rewriting the sibling's response id was
   rejected: a follow-up anchored on the first id would resolve to an owner
   that never ran it. The buffer is disarmed once output is downstream-visible
   and is never armed for an owner-bound request, the single account/model
   replacement, or a forced-refresh attempt.

5. Prove the contract at public HTTP and WebSocket routes. Unit checks remain
   supporting evidence; route tests cover temporary refresh preference,
   exhausted failover envelope preservation, and the HTTP 404 response.

## Risks / Trade-offs

- [A temporary preference is treated as an owner] -> The shared predicate
  deliberately excludes `force_refresh_account_id` and bare preference.
- [A later failure is masked by the old rejection] -> Clear fallback state as
  soon as a replacement connection is selected.
- [A response is replayed after acceptance] -> Keep the existing
  `awaiting_response_created`, response-id, and visible-output gates unchanged.
- [A client waits on `response.created` before output] -> Only a fresh
  replay-eligible stream defers it, by at most the upstream's own time to first
  output; keepalive comments still flow and the owner is published on parse.

## Migration Plan

No data migration or deployment migration is required. Rollback is the focused
GitButler tail commit; the existing pre-change route behavior is restored by
reverting that commit.
