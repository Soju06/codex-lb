# Evidence and safety boundary

The observed sequence is: a 38 MB request bypasses a 14 MB bridge budget; the
owner returns HTTP 429 before output; deterministic failover excludes the owner;
selection then rejects the same excluded account because turn-state still makes
it required. The route regression reproduces that exact state transition.

Payload size does not grant replay permission. Release uses the existing durable
full-resend verifier (`_verify_durable_full_resend`): stored-prefix matching,
retained prior output, and API-key scope. The replacement body is the bridge's
own account-neutral projection (`project_responses_input_for_account_neutral_fresh_replay`
followed by `responses_payload_is_account_neutral_fresh_replay`).

## Why the owner still gets the first attempt

Stripping aliases before the first attempt would lose owner selection and the
upstream session and turn-state stickiness on exactly the largest payloads. The
aliases are removed only after the owner is lost to quota, so a healthy owner
sees no change. This matches the two existing escapes: the bridge requirement
"Verified full resend can recover from selection-time owner loss" and
`_move_verified_fresh_replay_from_owner` on the stream path.

## One predicate, one state

The stream path already carries one verified replacement body,
`verified_fresh_replay_payload`, for a locally verified `previous_response_id`
resend. Every site that lets a required owner move now asks one predicate,
`_verified_owner_replay_available`, instead of repeating the inline check. The
turn-state proof fills the same state through `_admit_turn_state_full_resend`,
which runs only on quota evidence: a pre-visible quota or rate-limit rejection
(HTTP or `response.failed` frame), or a selection-time owner loss that
`_owner_selection_loss_is_quota_caused` attributes to quota. That helper was
`_compact_owner_selection_loss_is_quota_caused`; it is renamed because the stream
path now uses it too. A flag on that state tells
`_move_verified_fresh_replay_from_owner` to strip the aliases, clear the
turn-state owner, re-derive affinity, and log
`http_fallback_verified_full_resend` instead of
`cross_transport_verified_fresh_replay`.

The selection-time sites keep main's result for a `previous_response_id`
request whose payload was already dispatched to the owner: the move keeps the
dispatched-payload pin there, so selection still fails closed.

At the failover sites the owner's quota health is written before the durable
lookup runs, so a client disconnect during the lookup cannot drop it.

## Lazy verification

The durable lookup, the fingerprint of the full input, and the projection run
only when quota evidence arrives, and at most once per request. A bypassed
request with a healthy owner pays nothing. Review measured about 109 ms of
blocking fingerprint work, which previously ran on every bypassed request.

## Reasoning-bearing resends

Issue #2455 and the PR body do not show the failing request's items, so it is
not known whether that resend carried reasoning. Codex full resends normally do.
The replacement therefore uses the bridge's projection, which omits reasoning
and search bookkeeping and strips upstream item ids, as the bridge requirement's
"Verified resend contains owner-bound reasoning" scenario describes.

## Conflict with the #2374 proposal: maintainer decision needed

`relocate-anchored-turns-across-accounts` (#2374) is a proposal on `main`; its
`decide_relocation` verdict is not implemented. Its spec delta says the verdict
MUST decline "before consulting any evidence" when the request is bound by
"turn-state ownership, or a session-identity binding", and calls these
"ownership facts that no request body can neutralize". Its scenario "File-pinned
and turn-state-owned requests never relocate" repeats that.

This change contradicts that line. It relocates a turn-state-owned request when
durable proof shows the body is a complete, account-neutral full resend. That
rule cannot stand beside #2374 as written: #2374's line 13 and that scenario
need an amendment, or this change should not land. The maintainer should decide.

The strongest evidence for amending #2374: the landed bridge requirement
"Verified full resend can recover from selection-time owner loss" already
removes the anchor, strips every downstream session and turn alias, and clears
hard affinity for a proven full resend. So #2374's line 13 already conflicts
with landed bridge behavior. This change makes the stream path consistent with
that landed bridge behavior; it does not add a new kind of exception.

If the amendment is accepted, the verdict's decline order would read:

1. Downstream-visible output declines first.
2. `single_account` and the file pin decline. Turn-state ownership and session
   identity decline unless a durable full-resend proof exists for the same owner.
3. Only definitive quota evidence qualifies for that exception.
4. The source is the client's full resend, projected and then checked with
   `responses_payload_is_account_neutral_fresh_replay`.

The proof would become one `RelocationInputs` field. #2374's tasks already list
`_stream_owner_bound_to`, `_move_verified_fresh_replay_from_owner`, and
`_VerifiedDurableFullResend._verify` as the sites that delegate to the verdict,
so no other code would move.

## Limits

The bridge rebinds its durable session to the replacement account, so its next
turn stays there. The raw HTTP path has no durable session to rebind. A next
turn that still sends the old owner's turn state goes back to that owner. If the
owner is benched for quota by then, the selection-time path moves it without
dispatching to the owner.

Forwarded owner requests, previous_response_id, files, response-owned items,
incomplete history, missing durable state, owner disagreement, non-quota
failures, and visible output remain fail-closed. Any failure inside the
verifier, including the durable lookup, is treated as unavailable proof: it is
logged and the owner's rejection surfaces as before.

## Example

Account A owns turn state `t1`. The client sends a 38 MB full resend with
`x-codex-turn-state: t1`. The request bypasses the bridge and goes to A with
`t1`. A answers 429 `usage_limit_reached` before output. The proof matches A, so
A is excluded, the aliases are removed, and the projected body goes to B.
