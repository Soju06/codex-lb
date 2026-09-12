# Context

## What the measurement actually showed

Three hand runs, 26 direct upstream calls, each run using a ~28,000-token
prefix carrying a fresh nonce so only the seeding account had ever sent that
content:

| observation | result |
|---|---|
| content seeded by account A, read by four *other* accounts | near-full hits: 28,032/28,168 · 27,136/27,328 · 28,416/28,588 |
| does `prompt_cache_key` gate the hit? | no — one arm hit with a *different* key, several missed with the *same* key |
| does the account gate the hit? | no — same-account repeats hit 1/5, other accounts hit 3/8 |
| hit rate | sporadic, ~20-40%, independent of caller |

The shape is a node-local prefix cache that is not partitioned per account.

## Consequences this diagnostic must not misstate

1. Namespacing the outbound `prompt_cache_key` per account is cache-neutral
   (hits are not key-gated) and therefore safe, but it does **not** produce
   cache isolation. The probe must never be read as evidence that it does.
2. The only lever that actually isolates is changing the content prefix per
   account.
3. Cross-account sharing is currently a **benefit**. A thread that fails over
   from A to B still hits A's warm prefix, worth ~27-28k input tokens per hit,
   which is why keyed traffic holds 91% cache despite 8-26% account switching.
   Turning it off while the accounts-per-conversation factor sits at 2.29-3.45
   is a net loss.
4. Every account in this pool belongs to one owner serving one team, so
   cross-account sharing is not a third-party data leak.

## Why the asymmetry in the verdict matters

A hit on a non-seed account is proof: that content existed nowhere before this
run, so a sibling reading it back means the cache spans accounts. The absence
of a hit is not proof of the converse. At a ~20-40% base hit rate, four
sibling calls miss by chance with probability 0.13 to 0.41 even when the cache
is fully shared. The verdict is therefore named `no_cross_account_hit`, not
`isolated`, and a run in which the seed itself never cached is reported as
`inconclusive` — without one same-account hit there is no evidence the prefix
was cacheable at all, so silence from the siblings says nothing.

This is also why the UI states that sporadic misses on the seed account are
expected: an operator who reads a seed miss as a broken probe will re-run it
and spend quota for nothing.

## Why the nonce has to be in the first line

Upstream matches on a *prefix*. A nonce appended after 28,000 tokens of fixed
filler would leave those 28,000 tokens byte-identical to the previous run, so
the second run would measure the first run's cache instead of cross-account
sharing — and would report a spurious hit on every account. The filler words
are derived from the nonce for the same reason.

## Operating it

Run it twice: once before changing outbound cache-identity scoping and once
after, on a quiet pool, with the same seed repetitions and sibling count so
the two tables compare. The run costs roughly `28,000 x (repetitions +
siblings)` input tokens — about 196k at the defaults. The shared hourly budget
allows three runs, which covers a before/after pair plus one retry.
