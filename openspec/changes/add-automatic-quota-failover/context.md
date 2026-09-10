# Quota continuity recovery context

The September 9 main update exposed existing resilience mechanisms in Settings.
The reduced patch keeps only exhausted soft-affinity cleanup and verified
continuity recovery; it no longer duplicates retry controls.

A quota-rejected full-history continuation may drop obsolete anchors when a
native retry permits a replacement and the body is proven account-neutral.
Incremental continuations requiring the old owner remain fail-closed.
Native transport budgets, deadlines, and hard ownership remain authoritative.
