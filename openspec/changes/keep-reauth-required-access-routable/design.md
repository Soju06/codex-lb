## Context

`reauth_required` currently conflates a known-bad refresh credential with an unusable access token. The change separates those concerns without adding another persisted status.

## Goals / Non-Goals

**Goals**

- Keep stored access tokens usable until their known expiry or an upstream rejection.
- Prevent proactive exchange of known-bad refresh material.
- Preserve hard account ownership and avoid same-request retry loops.
- Retain compare-and-set and peer-rotation safety.

**Non-goals**

- Access-token-only import support.
- Access-token expiry prediction.
- New settings, statuses, migrations, or operator workflows.
- Routing paused or deactivated accounts.

## Decisions

### Centralize status eligibility

`account-routing` owns the status matrix used by proxy selection and ordinary access-token-authenticated supporting operations. This avoids repeating the same status rule in every projection and scheduler contract.

### Keep refresh eligibility separate

`reauth_required` suppresses proactive refresh. Forced refresh re-reads current state: it adopts a genuine peer rotation, uses current ciphertext as the compare-and-set guard for unchanged non-terminal material, and fails closed on unchanged terminal material.

### Quiesce on known access-token expiry

Selection derives the stored access token's expiry once when it builds in-memory account state. An unexpired `reauth_required` account remains routable; after the known expiry, selection and bridge reuse reject it locally. Tokens without a parseable expiry keep existing behavior rather than being guessed dead.

This deterministic gate was chosen over a health penalty because it avoids one upstream failure per backoff cycle and needs no new state or setting.

### Keep pre-expiry failure exclusion request-scoped

A permanent forced-refresh failure releases the current lease and excludes that account from the request's remaining movable retries. It does not create a process-wide unavailable mark before known access-token expiry.

### Preserve affinity without retrying dead credentials

A refresh warning does not prove owner loss while the access token is unexpired. Sticky, bridge, file, response, and realtime continuity therefore remain bound to the same account. At known expiry, bridge reuse stops; soft affinity may fail over, while hard account-owned continuity remains fail-closed.

## Risks

- A token without a parseable expiry can still require one upstream rejection; request-local exclusion prevents same-request loops.
- Mixed-version replicas may disagree during rollout; replace old replicas promptly.
- Broader eligibility changes aggregate totals; regression tests cover affected surfaces.

## Migration

No data migration is required. Existing `reauth_required` rows become routable after cache convergence.
