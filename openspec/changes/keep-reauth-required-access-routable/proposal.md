## Why

A permanent refresh-token failure does not prove that the stored access token is already unusable. Hard-blocking `reauth_required` accounts immediately discards otherwise valid request capacity and breaks existing account-scoped continuity even though ordinary upstream requests may still succeed.

## What Changes

- Treat `reauth_required` as request-routable while continuing to block proactive and background refresh-token exchange.
- Preserve sticky and HTTP-bridge ownership for `reauth_required` accounts; only paused, deactivated, deleted, or otherwise hard-unavailable accounts leave routing.
- On an upstream rejection, exclude the affected account from the current request's retry pool so a movable request can fail over without selecting the same account again.
- Keep forced refresh fail-closed for unchanged terminal refresh material, while adopting a freshly rotated peer row before exchange when one exists.
- Include request-routable `reauth_required` accounts in account-scoped usage, reset-credit, warmup, automation, and dashboard pace surfaces.
- Preserve `deactivated` as the hard authentication/account shutdown state.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Separate request routability from refresh-token eligibility for `reauth_required` accounts.
- `usage-refresh-policy`: Skip proactive refresh for `reauth_required`, reconcile fresh terminal/rotated rows before forced exchange, and fail over per request after permanent refresh failure.
- `sticky-session-operations`: Preserve sticky and durable HTTP-bridge ownership when an account becomes `reauth_required`.
- `rate-limit-reset-credits`: Allow request-routable `reauth_required` accounts to fetch and consume reset credits.
- `proxy-warmup`: Include request-routable `reauth_required` accounts in warmup target pools.
- `automations`: Permit automation dispatch to request-routable `reauth_required` accounts.
- `frontend-architecture`: Include fresh `reauth_required` account data in weekly credit pace calculations.
- `fleet-summary`: Permit fleet-triggered usage attempts for request-routable `reauth_required` accounts.

## Impact

This changes account selection, refresh preflight, retry exclusion, affinity lifecycle, usage/reset-credit eligibility, warmup, automations, and dashboard projection calculations. It adds no settings, schema changes, migrations, dependencies, or new setup steps. Existing `reauth_required` rows become routable after cache convergence; `deactivated` rows remain excluded.
