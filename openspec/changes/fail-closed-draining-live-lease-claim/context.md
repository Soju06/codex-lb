Turn-state owner-forward already refuses takeover while a DRAINING owner
still holds a live lease. Durable `claim_session` used a weaker predicate:
`DRAINING` or `CLOSED` counted as `state_allows_takeover`, so
`allow_takeover=False` was ignored. `_http_bridge_allow_durable_takeover`
had the same hole and fed local session create.

The live-owner check already exists: `_durable_bridge_lookup_active_owner`
returns None for closed, missing owner, or expired lease. Claim and local
create now use that rule. CLOSED, expired, and ownerless DRAINING rows stay
recoverable after the draining replica finishes or dies.

Example: instance A marks its session DRAINING during preStop with 60s left
on the lease. Instance B claims the same session key with
`allow_takeover=False`. Owner stays A and state stays DRAINING.
