# Context

## Why the walk is not bounded by counting the pool

Counting usable accounts would need a new selector API, and
`app/modules/proxy/load_balancer.py` sits at 3000 of its 3021-line architecture
ceiling. The invariant the retry loop already maintains is enough: the
request-scoped excluded-account set is monotone and the account table is finite,
so a walk that must grow that set on every `failover_next` terminates. Selection
already fails with `no_accounts` or `usage_limit_reached` once everything usable
is excluded — that failure *is* the pool-exhaustion signal, and it is the same
signal `probe_pool_usage_exhaustion` reads.

The runaway ceiling is therefore not the primary bound. It exists because a
selector bug that keeps returning fresh-looking accounts would otherwise spin
until the request deadline; the monotone-progress check catches the common shape
of that bug directly and logs it, and the ceiling catches whatever the check
does not.

## Why the classifier gains a field instead of a sibling — or a reordering

The downstream fork `aafqaq/codex-lb-enhanced` solved the same
capacity/exhaustion ambiguity by adding a second classifier
(`account_exhaustion_code_for_failover`) next to the existing one and calling it
from the selection paths. That leaves two sources of truth for "what kind of
failure is this" and forces every call site to know which one applies.

Reordering the existing classifier instead is not available either, and that is
a deliberate constraint rather than an oversight: "Model-capacity messages are
retryable transient failures" already carries two scenarios — "Quota and
rate-limit codes retain their stronger classification" and "Classified quota
failures still use the model-capacity replay wait" — that specifically require a
coded envelope to keep its code-derived class while still taking the capacity
wait. Health handling depends on that.

So the two questions are genuinely different and both are legitimate:
*how should this account's health be recorded* (unchanged) and *may this account
be excluded for the rest of the request* (new). One classifier answers both, in
one call, by returning both. The failure class keeps its current meaning and the
selection path stops inferring exclusion from it.

## The live bug this change fixes

A code-less upstream HTTP 429 normalizes to `upstream_error` and classifies
`retryable_transient`, which makes `is_upstream_burst_rejection` true. If that
429's message actually says the account's usage limit has been reached, the
proxy currently applies bounded same-account backoff — 1 s, 2 s, 4 s — to an
account that is out of quota, and then surfaces the rejection. Every second of
that wait is spent on an account that cannot serve the request. After this
change the message decides: the account is marked rate-limited and the request
moves on.

## What the first implementation attempt found

Three defects in this proposal only became visible once code existed.

The exclusion answer was specified as an exhaustion answer. "Is this account
exhausted" and "may the walk move off this account" are different questions, and
the walkable set includes `retryable_transient` — so a code-less burst 429, which
"An unbound burst rejection walks instead of surfacing" requires to be excluded,
and a model-capacity 429, which must not be, both come back false from an
exhaustion field. The requirement now states the selection predicate directly.

The terminal probe could not answer inside the request. `pool_usage_exhaustion`
needs a status *and* an at-or-above-limit usage sample; `handle_quota_exceeded`
writes the sample, `handle_rate_limit` does not, and the only other supplier is a
debounced background refresh that cannot land before the terminal probe of the
request that provoked it. So "every account exhausted yields the canonical pool
rejection" was unreachable through the mechanism this change mandates — for
coded rejections too, not only message-derived ones. That is now its own
requirement rather than an assumption.

The runaway ceiling was smaller than the fleet. A ceiling of 16 on a 28-account
pool is not a runaway fence, it is the fixed attempt cap this change exists to
remove, wearing a different name. The requirement now binds the ceiling to
exceed the largest supported pool.

A fourth, smaller one: the monotone-progress proof has a live counterexample in
the function the walk will live in — the account-capacity recovery path discards
its own exclusion to wait out a local cap. That re-admission is legitimate and is
now carved out explicitly, because a progress check that fires on it would break
a recovery path that works today.

## Worked example

A 28-account pool. A client sends an unbound `/v1/responses` turn.

| Today | After |
|---|---|
| Account A returns HTTP 429 (`usage_limit_reached`). Classified `rate_limit`. `candidates_remaining` starts at 3. | Same classification, same health write. |
| B and C also return 429. `candidates_remaining` reaches 0. | B and C are excluded. The walk continues to D. |
| `failover_decision` returns `surface`. The client receives **account C's verbatim upstream 429**, while 25 accounts were never asked. | D serves the turn. The client receives a response. |
| | If all 28 are exhausted, the probe confirms it once and the client receives the canonical `usage_limit_reached` 429 with `error.resets_at`. |

## Failure modes considered

- **Amplifying an upstream incident.** During a fleet-wide upstream rejection
  wave a request may now attempt many accounts instead of three. It stays inside
  the same request deadline, and each attempt replaces a client-visible failure
  that the client would have retried anyway. The per-account burst cooldown
  still steers fresh selection away from recently rejecting accounts.
- **Burning healthy accounts on a model-capacity rejection.** This is why the
  classifier change is a prerequisite and not a follow-up: without it, a
  capacity 429 would rotate the whole pool for a condition no account can serve.
- **Double health writes.** The walk must not turn one rejection into N
  penalties. The write stays on the `failover_next` branch, and the property
  test asserts one write per attempted account.
- **Drain strategies.** `probe_pool_usage_exhaustion` declines to answer under
  drain routing strategies, and the terminal path returns the preserved
  per-account failure in that case, so draining deployments keep byte-identical
  responses.

## Out of scope

Owner-bound requests — those carrying a required previous-response owner, a
file pin, or turn-state ownership — still cannot move, so this change does not
help them. Making an anchored conversation relocatable by rebuilding its context
from the durable operation spool is the separate
`relocate-anchored-turns-across-accounts` change. The two compose: this change
decides *whether* a request may walk, that change widens *which* requests can.
