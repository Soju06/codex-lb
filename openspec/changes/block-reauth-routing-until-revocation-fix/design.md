## Context

`reauth_required` records currently remain request-routable while their stored access-token JWT has a future `exp`. Production evidence shows that upstream can revoke that token before `exp`; affected records then cycle through transient backoff and sticky reuse while returning `401 token_revoked`.

## Goals / Non-Goals

**Goals:**

- Stop all new upstream I/O through `reauth_required` accounts.
- Apply the safeguard to ordinary selection and live HTTP bridge reuse.
- Preserve account ownership invariants and recover only through re-authentication.

**Non-Goals:**

- Distinguish refresh-token invalidation from access-token revocation.
- Add a credential-generation field, status, setting, or migration.
- Rebind hard response, file, conversation, or turn-state ownership across accounts.

## Decisions

The temporary policy restores status-based exclusion at the two canonical proxy gates: load-balancer candidate filtering and bridge-session reuse. Existing status and token-expiry fields remain unchanged, so OAuth re-authentication continues to repair the account through the established path.

This is preferred over treating `token_revoked` as a transient failure because backoff deliberately admits probes later and therefore repeats a known authentication failure. It is also preferred over mapping the error to `deactivated`, which would conflate invalid credentials with an upstream-disabled account.

Soft prompt-cache affinity may select another active account through existing fallback behavior. Hard ownership remains fail-closed when its owner requires re-authentication.

## Risks / Trade-offs

- [Risk] A `reauth_required` account whose access token would still work loses temporary capacity. → Re-authentication restores it, and the upstream issue tracks a targeted credential-state solution.
- [Risk] Mixed-version replicas disagree during rollout. → Replace all application slots through the normal blue/green deployment when this hotfix is deployed.
- [Risk] Existing tests encode the recently introduced routable-warning policy. → Update only proxy selection and bridge-reuse expectations covered by this safeguard.

## Migration Plan

No data migration is required. Deploy the code through the existing blue/green path; cache convergence and bridge reuse checks stop further admissions. Rollback restores the previous code and does not require data repair.

## Open Questions

None for the temporary safeguard. The upstream issue owns the durable distinction between refresh-token and access-token usability.
