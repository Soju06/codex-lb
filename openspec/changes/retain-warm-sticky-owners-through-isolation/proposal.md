# Change: retain-warm-sticky-owners-through-isolation

## Why

Releasing a soft sticky owner during overload isolation is a **rebind**, not a one-time move: `reallocate_sticky = True` becomes `_StickyMutation(account_id=None)` and the fallback is re-persisted to the sibling. Nothing ever returns the thread when isolation lifts, so the next isolation event rebinds it again. One conversation therefore accumulates one permanent new account per isolation episode it touches.

The deployment's `request_logs` show exactly that shape. For conversations with at least three requests, the mean number of distinct accounts per conversation was **2.29 on a healthy upstream day (09-09, 1,929 conversations, 45 % single-account, max 22)**, **3.45 during the 09-10 upstream incident (2,674 conversations, 42 % single-account, max 26)**, and **1.02 when upstream was quiet (09-11, 326 conversations, 99 % single-account, max 3)**. Session-level figures match (2.36 / 3.48 / 1.02). The factor tracks upstream pressure, not a structural defect — it is failover and soft-sticky rerouting, and the rebind is what makes each episode's cost permanent.

The rebind buys nothing. The request goes to a healthy sibling either way; only the *bookkeeping* differs. And the usual justification — "the sibling is cold, so commit to it" — does not hold on this deployment: direct upstream probing (three runs, 26 calls, ~28,000-token prefixes carrying a fresh nonce) showed content seeded by one account producing near-full cache hits on four *other* accounts (28,032/28,168 · 27,136/27,328 · 28,416/28,588), reproduced in all three runs. Hits are gated by neither `prompt_cache_key` (one arm hit with a different key, several missed with the same one) nor the account (same-account repeats hit 1/5 while other accounts hit 3/8). The prefix cache is node-local and not partitioned per account, so a thread that is served by a sibling can still hit its owner's warm prefix. Keeping the mapping costs nothing and reclaims the locality on the turn the owner recovers.

## What Changes

- **The overload-isolation release of a soft sticky owner becomes request-local.** A `prompt_cache`, `sticky_thread` or `codex_session` mapping pinned to an isolated account still routes *this turn* to an overload-free sibling — availability is unchanged — but the sticky row is no longer deleted or rebound. The thread returns to its warm owner on the first turn after isolation lifts, so it sees at most `{owner, substitute}` per episode instead of gaining an owner per episode forever. This is the same rule bare-session cap spillover and request-local retry pressure already apply; isolation simply stops being the exception.
- **The substitute is stable across turns.** This is the correctness condition, not a refinement: because the release is request-local, the replacement is re-picked on every turn, and a weighted-random draw would bounce the thread across siblings — strictly worse than the single rebind it replaces. `deterministic_isolation_substitute` hashes the sticky key over the sorted overload-free pool, so one thread gets one substitute, distinct threads spread across the siblings instead of herding onto the single best account, and replicas that observe the same pool converge without shared state. The pick is then put through the real selector as a single candidate, so strategy, health, quota and budget gates stay authoritative and an ineligible substitute simply falls back to the pool pick.
- **Explicit reallocation still wins.** `reallocate_sticky` is an instruction to retire the mapping, and the isolation branch reuses that same local further down, so the caller's intent is captured before it is overwritten. An isolation reroute on a reallocating request rebinds as before and the diagnostic says `mapping=rebound`.
- **Isolation does not convert a request-local spillover into a rebind.** An owner that is isolated *and* unavailable for request-local reasons only -- filtered by the per-account concurrency caps, or excluded by this request's own retry loop while still recoverable and in continuity scope -- keeps its mapping, exactly as it already does when it is not isolated. This is why the two `and not owner_overload_isolated` suppressors on the existing preservation gates are dropped.
- **Retention is bounded by recoverability.** It applies only while the pinned owner's status is `active`, `reauth_required`, `rate_limited` or `quota_exceeded`. A `paused`/`deactivated` owner, an owner absent from the request's pool, and an explicit `reallocate_sticky` all keep the existing rebind path and release the mapping on that turn.
- **Hard continuity owners are untouched.** `previous_response_id`, turn-state rows, bridge ownership, file pins and legacy raw keys are resolved before soft selection; the isolation path never sees them.
- **The `sticky_owner_overload_isolation_reroute` diagnostic gains `mapping=retained` and `substitute=deterministic|weighted`**, so the accounts-per-conversation change is attributable in logs and the fallback-to-weighted rate is observable. It still carries no account identifiers.

No new setting, no schema change, no migration; the settings ratchet is untouched.

### Expected effect

This removes the *accumulation*, not the *episode*. A thread that touches N isolation episodes should converge from `1 + N` distinct accounts toward roughly 2 — the owner plus its substitute — with the substitute repeating rather than being redrawn. Against the incident-day baseline of 3.45 that is the dominant term; against the healthy-day 2.29 it is a smaller one, because on a healthy day most of the factor is ordinary failover rather than isolation rebinding. It does not target the quiet-upstream case, which already measures 1.02.

### What this change deliberately does not do

- **No same-owner retry (audit lever A).** `overload_backoff.py` documents that an overload rejection takes 30-90 s to arrive, and per-account rejection ran at 96-100 % during the incident's bad phase, so a blind same-owner retry pays ~0.96 × 30-90 s for a ~4 % chance against a reroute whose cache cost is near zero. Only a hedged variant with a short first-token deadline is defensible, it must ship disabled, and it would collide textually with #2351/#2360 in `_service/streaming/`. Out of scope here.
- **No cache-value weighting by input size (audit lever B).** Hit probability is not account-gated, so the quantity it would weight barely varies with the decision. `StickySelectionRequest.estimated_lease_tokens` saturates at 8,192 by construction and is not forwarded into `_select_with_stickiness` anyway; `_payload_size_estimate_bytes` systematically *understates* exactly the long `previous_response_id` threads worth protecting, because their history lives upstream. If a cheap signal is wanted later, `StickySession.created_at`/`updated_at` are already materialised by `get_entry` and cost no extra query.
- **No change to when isolation ends.** Isolation is self-sealing — `filter_overload_backoff_candidates` keeps fresh admissions off the account, so no recovery evidence can arrive and the full window always elapses. An evidence-based exit (a bounded fresh-admission probe on the existing `HEALTH_TIER_PROBING` machinery, with an admission-scoped counter because `recent_outcomes()` is contaminated by warm-session successes) is the follow-up that should reach the factor the rest of the way, and lowering `proxy_overload_isolation_seconds` from 1800 is only safe after it. Kept separate so this change can be measured on its own.

### Risks

- **Substitute convergence across replicas is best-effort, not solved.** Each replica keeps its own `_runtime` and therefore its own view of which siblings are overload-free. Hashing the sticky key over the sorted pool makes replicas agree *given the same pool*, but the pools themselves differ, so the same thread can hold a different substitute per replica. This caps the fan-out at one substitute per `(replica, pool)`; it does not eliminate it.
- **Substitute stability is load-bearing.** If the deterministic pick is repeatedly vetoed by the selector, the code falls back to a weighted draw and per-turn re-selection reappears — the failure mode the comment at the budget-reallocation site warns about. The `substitute=weighted` marker on the diagnostic exists so that rate is visible in production rather than inferred.
- **The measurement cannot be a day-over-day mean.** 09-11 already shows 1.02 with no code change, so shipping into a quiet upstream will look like success regardless. Validation needs an isolation-event-scoped metric (threads owned by an account at the moment it trips, and how many distinct accounts they touch over the next hour) or a held-back cohort.
- **33-38 % of requests carry neither `conversation_id` nor `session_id`** (32,828 / 30,237 / 3,320 on the three days) and are invisible to this metric; their turn-to-turn switch rate is 38-60 % regardless of pressure. #2347 owns that traffic. Driving the measured factor down while a third of requests keep switching is a real possibility and the headline number must not stand in for the whole problem.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: REMOVED requirement "Isolated accounts release their soft sticky owners", replaced by ADDED "Isolated accounts release their soft sticky owners for one request" — the release becomes request-local and the substitute must be deterministic per sticky key. It is a remove/add rather than a MODIFIED block because two of the five existing scenarios assert a rebind and had to be renamed, which a MODIFIED block cannot express (it may not drop a scenario the current spec still has). All five are carried over; three new ones cover substitute stability, the recoverability bound and hard owners.

Cross-references (no MODIFIED block): `account-routing` "Upstream overload rejections back off and then isolate the account" already delegates the soft-owner rule to `sticky-session-operations` ("established soft sticky owners MAY be released as specified by ...") and needs no edit; "Routing weights and overload isolation are dashboard settings" is unchanged because this change adds no knob. The pending change `preserve-soft-sticky-owner-on-request-local-unavailability` lists the isolation stage as a permanent-removal case in its ADDED requirement; that clause is corrected here in the same commit.

## Impact

- Code: `app/modules/proxy/_load_balancer/overload_backoff.py` (one new pure helper plus the module docstring), `app/modules/proxy/_load_balancer/sticky_selection.py` (the isolation branch picks a deterministic substitute, sets a request-local flag instead of a mutation, and two `not owner_overload_isolated` suppressors on the existing preservation gates are dropped). No change to `load_balancer.py`, `_service/streaming/`, the schema, or the settings surface.
- Data: one fewer sticky delete + upsert per isolation-affected turn.
- Operators: no action and no new setting. `sticky_owner_overload_isolation_reroute ... mapping=retained` is the line to count; a rising `substitute=weighted` share means the deterministic pick is being vetoed and the stability property is degrading.
- Conflicts: none expected with #2360 or #2351 — both touch `_service/streaming/{helpers,mixin,retry}.py`, which this change does not.
