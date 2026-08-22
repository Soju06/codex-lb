## Context

`AccountStatus.REAUTH_REQUIRED` currently carries two meanings: refresh-token exchange is known to require operator repair, and the account is treated as wholly unavailable for ordinary requests. Those meanings are coupled across selectors, cache invalidation, affinity cleanup, usage-related surfaces, and retry handling. See `proposal.md` for motivation.

Refresh tokens rotate and may fail independently of an already-issued access token. Request routing therefore needs a status policy distinct from refresh eligibility. The existing `DEACTIVATED` state remains the hard account/session failure signal.

## Goals / Non-Goals

**Goals:**

- Make request routability and refresh-token eligibility explicit and consistent across all account consumers.
- Preserve owner-bound continuity while an access token remains usable.
- Prevent permanent refresh failure from reselecting the same account within one request.
- Keep token refresh compare-and-set and peer-rotation safety intact.

**Non-Goals:**

- Add access-token-only import support or change token storage schema.
- Automatically infer access-token expiry without an upstream request.
- Introduce a new status, setting, migration, or operator workflow.
- Make `DEACTIVATED` or `PAUSED` accounts routable.

## Decisions

### Separate refresh eligibility from request routability by status policy

`REAUTH_REQUIRED` joins `ACTIVE` for ordinary request selection and account-scoped request surfaces, but remains terminal for proactive refresh. `DEACTIVATED` and `PAUSED` remain hard routing exclusions.

A new persisted status was considered, but rejected because the existing state already communicates the operator action accurately and adding a state would require migration and dashboard compatibility work. Treating every refresh failure as `DEACTIVATED` was rejected because it would again discard a potentially valid access token.

### Keep retry exclusion request-scoped

After upstream rejection causes forced refresh to fail permanently, movable retry loops add the account ID to the current request's exclusion set and release its lease before selecting again. They do not write a process-wide routing-unavailable mark for `REAUTH_REQUIRED`.

This avoids an immediate same-account retry loop while retaining capacity for independent requests whose access token may still work. A permanent local mark was rejected because it recreates the behavior this change removes.

### Preserve affinity for warning-state accounts

Status persistence, sticky selection, durable bridge reuse, realtime-live ownership, and continuity selection treat `REAUTH_REQUIRED` as a recoverable request owner. Only hard-unavailable transitions clear durable affinity and bridge ownership.

Rebinding on `REAUTH_REQUIRED` was rejected because an access-token refresh warning does not prove owner loss and cross-account rebinding can violate upstream continuity.

### Freshly reconcile claimless forced refresh

When no refresh-claim coordinator is available, forced refresh first reads the latest row. A changed plaintext fingerprint adopts a peer rotation without exchange; the same plaintext under new ciphertext becomes the current CAS guard; unchanged terminal material fails closed without exchange.

Blindly retaining the previous claimless path was rejected because forced refresh of `REAUTH_REQUIRED` could re-exchange known-bad single-use material. Comparing ciphertext alone was rejected because encryption is nondeterministic.

### Apply one eligibility policy to adjacent account surfaces

Warmup, automations, reset credits, API-key pools, usage identity, force probe, and weekly pace include `REAUTH_REQUIRED` wherever they perform an ordinary access-token-authenticated request or summarize routable capacity. Background credential guardians continue to exclude terminal refresh states.

## Risks / Trade-offs

- **[Expired access token can produce one upstream rejection per independent request]** → Each request excludes the account after forced refresh fails, allowing failover without looping; the dashboard continues to show reauthentication required.
- **[Mixed-version replicas disagree on affinity cleanup]** → New replicas clear legacy local routing overlays during cache convergence, but deployments should replace old replicas promptly because an old replica can still hard-block and tear down bindings on a new `REAUTH_REQUIRED` transition.
- **[Broader account eligibility can change pooled totals and dashboard pace]** → Use the same active-or-reauth policy across selectors and summaries and cover the changed totals with regression tests.
- **[Claimless preflight changes CAS attempt ordering]** → Assert outcomes and fresh guard ownership rather than requiring an avoidable stale CAS miss.

## Migration Plan

No data migration is required. Deploy all replicas together where practical. Existing `reauth_required` rows become request-routable after account-routing cache convergence; no status rewrite is needed.

Rollback restores hard blocking. Existing data remains compatible, but affinity created or preserved while the new version ran may remain until normal cleanup because rollback alone does not create a new status transition.
