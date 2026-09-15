## Context

The findings concern access-token rejection, not refresh-token validity. Refresh ciphertext still provides a useful compare-and-set fence, but changing refresh material does not prove access repair. Separately, rate/quota writes use an unguarded status writer and can arrive after rejection commits.

## Decisions

- Compare only access-token plaintext for rejection staleness; keep both latest ciphertexts in the final atomic write predicate.
- Only changed access material can clear the rotation's local routing-rejection marker.
- Preserve current proven rejection in the database status update for nonterminal incoming health states using an atomic expression. Continue recording cooldown fields and allow explicit terminal operator changes. Use the actual stored status for sticky-session transition hooks.
- Have the balancer adopt freshly persisted status/reason/cooldown fields, instead of copying the requested health state over the account snapshot.
- Reuse the fleet updater's encryptor and canonical access-eligibility predicate before computing attempted count.

## Boundaries

No new locks, database schema, retry policy, or configuration. The earlier post-commit rotation publication race is not solved by this change. Refresh warnings do not gain permission to exchange refresh tokens. Paused/deactivated and pending-deletion behavior remains independent.

## Verification

Use real database transactions and actual balancer/bridge/selection paths for rejection ordering. Exercise the fleet HTTP endpoint with the real updater and only the upstream usage fetch stubbed. Retain repair/operator CAS tests and reverse-order cooldown tests.
