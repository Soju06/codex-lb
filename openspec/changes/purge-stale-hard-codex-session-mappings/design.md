# Design

## Why the hot path is untouched

`load_balancer.py`'s `hard_sticky` branch never enters the soft-reallocation
code (recovery-sleep waits, budget-pressure reallocation, TTL rebinding) by
design: a `codex_session` pin can represent live, unverifiable state (an
in-flight tool call, account-scoped uploaded files, opaque CLI turn state)
that another account cannot safely take over mid-session. Unlike the
`previous_response_id` fixes in the same problem space, there is no
stored-object replay to verify here — nothing proves the client's next
message is a safe fresh start rather than a continuation of something only
the original owner can resolve. So request-time selection must keep failing
closed for an unavailable hard owner, exactly as today.

## Why a periodic purge instead of a threshold inside selection

Threading a "give up after N hours" check into the hot-path `hard_sticky`
branch would make every selection call pay for a purge decision it usually
doesn't need, and would tangle a background-cleanup concern into
correctness-critical selection code that's already carefully scoped (see the
comment at `load_balancer.py:866-869` about never entering sticky fallback
code from the hard branch). The existing `StickySessionCleanupScheduler`
already runs leader-elected, every 300 seconds, purging prompt-cache and
bridge-session rows the same way. Adding one more purge query to that same
cycle keeps the correctness-sensitive selection path completely unchanged
and reuses infrastructure that's already tested for leader-election
correctness and background-session handling.

## What clock to gate the cutoff on

The first draft gated on `Account.reset_at`/`blocked_at` — plausible in
theory (they look like "when will/did this become unavailable" fields), but
wrong against real data: `reset_at` is frequently `None` (upstream simply
hasn't reported fresh quota data recently), and `blocked_at` is explicitly
reset to `None` whenever an account is paused (`accounts/service.py`'s
`pause_account` and the auto-pause-on-import path both pass
`blocked_at=None`). Neither field reliably answers "how long has this
specific account been broken" across all three unavailable statuses.

`StickySession.updated_at` does, indirectly but reliably: it only advances
when the mapping is actually reused on a successful request, so it already
means "how long since this session last worked" without needing any
per-status account bookkeeping. Gating on `Account.status` (non-active) AND
`StickySession.updated_at < cutoff` together also gets a property the
account-field approach didn't: a session that's still being actively used
right up until the moment its owner goes down keeps its full grace window
from that last real use, not from whatever moment its account record
happened to change.

## Choosing the threshold

Six hours is deliberately far longer than any ordinary quota-reset window
(Codex quota windows are typically minutes to a few hours) so a transient
blip — the exact case the "does not lose its mapping" scenario protects —
never has its mapping purged mid-window. It only fires once an account has
been stuck well past when it should have recovered on its own, which is a
strong signal something is actually wrong (manual pause left in place,
reset_at data stale, etc.) rather than ordinary quota cycling. This is a
fixed constant, not a new setting, matching this scheduler's existing
"poll cadence is fixed; issue #1340 / PRINCIPLES.md P2" convention and the
simplicity-gate norm of defaulting new behavior on without adding
configuration surface.

## Why delete, never rebind

Rebinding would require deciding *which* account to rebind to and asserting
that account can safely inherit whatever live state the mapping represents —
exactly the thing this whole subsystem says isn't verifiable for a hard
`codex_session` pin. Deleting sidesteps that entirely: the mapping simply
stops existing, and the next request that would have resolved it instead
goes through ordinary fresh selection, identical to a session no one has
ever pinned. This is the same effect the existing reauth/deactivated cascade
delete already produces for those two statuses — this change only extends it
to the two recoverable-but-stuck statuses, gated on elapsed time instead of
firing immediately.
