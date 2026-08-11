## Why

This change originally proposed giving the HTTP bridge *owner* request itself
a stuck-gate failover: when the owner produced zero response events for too
long, retire its session, exclude that account, select a fresh eligible
account, and resubmit — mirroring what the existing gate-timeout *waiter*
path already did for a second request stuck behind the same gate.

Since this was first proposed, `recover-fresh-hard-bridge-timeouts` (shipped
as part of #1394, "stabilize silent and clean-close recovery") landed a
bounded eventless `response.created` watchdog that does almost exactly this
for the owner request: when a hard, pre-response request with no
previous-response id, continuity anchor, proxy-injected anchor, or
account-scoped file ownership reaches that watchdog with zero response
events, pre-response recovery excludes the failed account and may resubmit
on a fresh one — see that capability's "Fresh hard bridge requests may
recover across accounts" requirement. That supersedes the core of what this
change originally asked for. Two differences worth naming rather than
silently re-implementing over: the watchdog's deadline is anchored to when
the create request was actually sent upstream (not the request's overall
`started_at`) and capped tighter than the flat stuck-gate threshold, and it
deliberately does not penalize the account's health — every failure path in
that mechanism treats "no `response.created`" as upstream-ambiguous, not
proof the account itself is bad. This proposal does not reopen either
design decision.

What's left, and still genuinely unaddressed, is on the *waiter* side of the
picture — a different, older code path
(`_http_bridge_can_replace_retired_gate_session`) that decides whether a
second request, timing out behind a session another request already wedged,
may be transparently resubmitted on a replacement bridge once that session
is retired:

1. A waiter whose `replay_count` is non-zero is disqualified from that
   replacement today. `replay_count` isn't a clean "client reconnected"
   signal — it's also incremented at proxy-side upstream resubmission
   points, so it says nothing about upstream progress on the *current*
   bridge attempt either way. The predicate's other markers (no response id,
   no response event, no downstream sequence number, not downstream-visible)
   already establish that the waiter is definitively unsubmitted; an
   otherwise fully unsubmitted waiter is exactly as safe to move regardless
   of its replay count.
2. The replacement session this path creates does not exclude the account
   whose gate just proved stuck, so the load balancer can legally reselect
   the exact same wedged account for the "replacement" — the same class of
   gap this change originally raised, just in a sibling function the
   eventless-watchdog rework didn't touch.

## What Changes

- Drop `request_state.replay_count == 0` from
  `_http_bridge_can_replace_retired_gate_session`'s guard. A waiter is
  disqualified by any response id, response event, downstream sequence
  marker, or visible output — never by replay count alone.
- When that predicate accepts a waiter for replacement and the replacement
  is not already pinned to a required account (no previous-response owner
  resolved, no file-pinned account), add the retired session's account to
  `request_state.excluded_account_ids` before building the replacement
  session, so the fresh bridge cannot legally reselect the account that
  just proved stuck. A waiter whose replacement *is* pinned must not have
  its required account excluded — that account's own unavailability is a
  separate, pre-existing failure mode this change does not touch, and
  excluding it here would make its own required-account replacement
  impossible and poison every later recovery call on the request, since
  `excluded_account_ids` persists on `request_state`.
- No changes to the owner-side eventless watchdog, its threshold, its
  account-neutral (no-penalization) treatment, or continuity-pinned
  recovery — all of that is `recover-fresh-hard-bridge-timeouts`'s territory
  and is left exactly as it is.

## Impact

- Affected capability: `proxy-admission-control`.
- A waiter with a non-zero `replay_count` now gets the same transparent
  gate-replacement path as one with `replay_count == 0`, provided its
  current bridge attempt is still definitively unsubmitted.
- An unpinned waiter's replacement bridge can no longer land back on the
  account whose gate it was just waiting behind.
- A pinned waiter's (continuity or file-owned) replacement stays pinned to
  its required account exactly as before — this change does not exclude it.
- No behavior change for continuity (previous-response-owner) waiters, which
  remain pinned to their required account.
- No behavior change for any request that has already produced a response
  id, response event, downstream sequence number, or visible output.
- No behavior change to the owner-side eventless watchdog added by
  `recover-fresh-hard-bridge-timeouts`.
