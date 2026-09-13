# Why

Shutdown marks durable HTTP-bridge rows `DRAINING` before releasing the lease
so the owner can finish an in-flight turn. A foreign
`claim_live_session(..., allow_takeover=False)` still steals that row because
`DRAINING` is treated as takeover-eligible even while the lease is live.
Turn-state owner-forward already fails closed. Local create and durable claim
do not, so two replicas can own one session during rolling drain.

# What Changes

- Treat a live DRAINING lease like a live ACTIVE lease for foreign claims.
- Align `_http_bridge_allow_durable_takeover` with the turn-state helper.
- Keep expired, released, and CLOSED rows takeover-eligible.

# Capabilities

### Modified Capabilities

- `responses-api-compat`: durable claim and local session create must fail
  closed on a live DRAINING lease.

# Impact

Turn-state forward-failure 503 stays as it is. Expired or ownerless DRAINING
rows can still be taken over after the draining owner releases or lapses.
