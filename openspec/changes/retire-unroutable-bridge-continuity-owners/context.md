# Context

## Purpose and scope

Give a durable HTTP bridge row a way to let go of an owner account that cannot serve it, so a
Codex thread stops returning 502 forever while healthy accounts sit idle. In scope: the marker,
the grace-gated sweep that writes it, the read semantics that honour it, and the exemption that
keeps it from becoming a different 502. Out of scope: retiring an owner *immediately* on the
request path, and moving a turn whose body is account-bound.

## Production evidence (`1.25.0-beta.7`, 24 accounts, 2026-09-11)

| Observation | Value |
|---|---|
| `previous_response_owner_unavailable` request-log rows | 1,480 / 36h |
| … owned by one `reauth_required` account | **1,361** |
| … owned by `paused` accounts | 22 |
| `draining` rows pinned to unroutable owners, frozen since the previous deploy | 6 |
| … of those owning operation rows (so unreachable by `purge_abandoned_before`) | **6 of 6** |

`reauth_required` is the single largest source and is in neither existing cleanup path: the
`DEACTIVATED` detach does not cover it, and `_HARD_STICKY_UNAVAILABLE_STATUSES` omits it.

## Decisions

**Why a marker and not a delete.** `sticky_repository.py:683-703` already argues this at length
and the reasoning transfers unchanged: with more than one account in the pool, a deleted row
makes the anchored selection path fail closed forever, because nothing on that path can
re-create the row it needs in order to stop failing closed. A marker distinguishes "deliberately
abandoned, pick a fresh owner" from "never seen".

**What the grace window protects: a live thread, not a young outage.** The sticky sweep needs
an outage clock because `StickySession` has no activity signal at all — its timestamp moves on
every pin refresh, so it cannot tell "owner died long ago" from "owner died just now". A bridge
row records when the thread last actually served, which answers the question the grace is
really asking: is this thread live enough for its anchor to be worth protecting? So a session
that served a turn inside the window keeps its owner however long that owner has been down,
while a session that served nothing across the window is retired as soon as its owner goes
unroutable, even on a brand-new outage.

The cost of that choice is bounded and explicit: a thread idle for six hours whose owner pauses
for two minutes loses its response anchor and re-sends its history on the next resume. The
alternative is that the same thread returns 502 for as long as the pause lasts. An anchor
nobody has touched in six hours is worth less than the thread being resumable at all.
`test_a_dormant_thread_is_retired_on_a_fresh_outage` and
`test_recent_activity_keeps_an_unroutable_owner` pin both halves.

**Why `last_seen_at` is the grace clock.** The sticky design carries two extra mechanisms —
`_refresh_hard_sticky_outage_grace` on the status transition, and a once-per-database
`runtime_sentinels` backfill so the first sweep after the feature ships cannot mistake a
pre-existing stale `updated_at` for a long-dead owner. Both exist because `StickySession`'s
timestamp moves for reasons unrelated to serving. The bridge row already records when the thread
last actually worked, so "unroutable owner and no successful turn for 6h" needs neither. This is
a deliberate simplification, not an oversight: if `last_seen_at` ever starts moving for
non-serving reasons, the sticky pair becomes necessary here too.

**Why the `~exists(operation)` guard is dropped from phase 1 only.** The guard exists because
`http_bridge_operations.session_id` is `ON DELETE CASCADE`, so deleting a session would silently
take its recovery ledger (and transitively its event rows) with it — the rationale is stated at
`durable_bridge_repository.py:3000-3003`. Retirement writes two columns and deletes nothing, so
the guard protects nothing there. Keeping it would have been fatal: all six frozen production
rows own operations, which is precisely why the 2026-09-04 incident had to be resolved by hand.

**Why `_to_lookup` is the single application point.** `durable_bridge_snapshot_is_detached`, the
#2063 filter, is applied at call sites and reaches two of the four lookup paths — the canonical
`get_session` and the alias loop — leaving both `find_session_by_latest_*` fallbacks uncovered.
`_to_lookup` is the one funnel every path already passes through.

**Why a retired lookup surrenders its anchors too.** An owner and a response anchor are one
fact, not two: the anchor points at upstream state only that account can read. Retiring the
owner while keeping the anchor would have the next request inject a pointer no replacement
account can resolve, turning a 502 into an upstream rejection.

**Why `reauth_required` is in the SQL set but not in `ROUTABLE_STATUSES`.** An account warning
about re-authentication is still routable while its stored access token is unexpired, and
`reauth_access_token_is_expired` needs a decrypted token, which SQL cannot do. Pairing the
status with a long inactivity grace is the substitute: an account that has been warning for six
hours without serving its thread is not coming back on its own, whatever its token claims.
Anything deciding routability *now* must keep using `ROUTABLE_STATUSES` plus the expiry check.

## Constraints

- No new setting; the grace reuses `_STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS`.
- No ceiling-guarded proxy file may grow (`service.py` 2600/2600, `load_balancer.py` 3000/3021,
  `_service/http_bridge/mixin.py` 2435/2436, `_service/streaming/mixin.py` 1097/1100). None is
  touched.
- The migration adds nullable columns with no backfill, so a replica on the previous build is
  unaffected during a rolling deploy.

## Failure modes

- **Retirement becoming a different 502.** Making `account_id` read as `None` feeds
  `durable_owner_missing`, which fails closed. The exemption in `streaming.py` is load-bearing;
  `test_v1_responses_http_bridge_resumes_a_thread_whose_owner_was_retired` is what catches its
  removal, because the unit layer alone would still pass.
- **Retiring a transient outage.** A rate-limited owner with a reset horizon in the future is
  evidence the owner returns, so waiting preserves the upstream prompt cache instead of forcing a
  full resend. Two unit cases pin both sides of that horizon.
- **Retirement sticking after recovery.** `claim_session` clears both markers on every successful
  claim; without that a thread would be permanently demoted to fresh starts.
- **The cleared marker resurrecting the anchor it masked.** A claim that cleared only the marker
  would leave the old response id, turn state, fingerprints and pending calls behind, and the
  next lookup would serve that abandoned anchor as ordinary continuity. This bites hardest when
  the retired account itself recovers and is selected again, because `account_changed` is then
  False. Reclaiming a retired row therefore discards continuity exactly as an account change
  does, aliases included.
- **Racing the durable write in tests.** The bridge persists its row off the request path, so a
  test that mutates the row straight after a 200 is flaky. The end-to-end test polls for the row
  first — the first draft of it failed once for exactly this reason.
- **The owning replica outliving the owner.** `_durable_bridge_lookup_active_owner` reads
  `owner_instance_id` and `lease_expires_at` off the lookup, so a retired row that kept them
  would still be forwarded to the replica that owned it. The sweep only retires rows idle for
  the whole grace window, so no lease can realistically still be running — but leaving them set
  would make the invariant depend on the bridge lease TTL staying below the grace window, a
  setting nobody would think to check before changing. `_to_lookup` masks both;
  `owner_epoch` is fencing state, not continuity evidence, so it survives.
- **Rolling deploy.** A replica on the previous build ignores both columns and keeps treating
  `account_id` as hard ownership, so the worst case during rollout is the behaviour that exists
  today.

## Two findings deliberately not acted on

**A client-supplied `previous_response_id` still fails closed.** Retirement frees
proxy-injected continuations — the common Codex shape, and the one in every production trace
for this bug, which all carry `previous_response_id=None`. A client that sends its own anchor
names upstream state that lived on the retired account, and no replacement can read it, so the
request still fails closed. That is the *same* outcome as before this change, reached by a
different branch, so it is not a regression. What is wrong about it is the error: 502
"retry later" invites a retry that can never succeed. Replacing it with an actionable,
non-retryable error is the follow-up; `test_a_retired_row_still_refuses_a_client_supplied_anchor`
pins the boundary so that follow-up is deliberate.

**The retired account is not excluded from replacement selection.** Excluding it looks safer
but buys nothing: retirement already stripped the anchors, so selecting the recovered former
owner starts a fresh turn exactly as any other account would. It is also the account most
likely to still hold this conversation's upstream prompt cache, so excluding it would force a
cache miss for no gain. While the account is still unroutable the selector skips it anyway.

## Example

A thread pinned to an account the operator paused, resumed after the sweep:

```text
# before
Proxy account selection start request_stage=follow_up preferred_account_id=<A>
No account selected error=No available accounts
Proxy preferred account unavailable error_code=continuity_owner_unavailable
proxy_error_response status=502 code="previous_response_owner_unavailable"

# after the sweep retires <A>
Retired durable HTTP bridge continuity owners that stayed unroutable retired_count=1
Proxy account selection start request_stage=first_turn preferred_account_id=None
Selected account_id=<B>
"POST /v1/responses HTTP/1.1" 200 OK
```

The thread loses its upstream prompt cache and re-sends its history, which is the cost the
operator chose over an unusable thread. If account `<A>` recovers and a later turn lands on it,
the claim clears the marker and ordinary hard ownership resumes.
