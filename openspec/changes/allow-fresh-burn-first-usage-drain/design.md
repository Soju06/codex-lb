## Context

Account runtime health is synchronized before routing-policy preference. An active account at or above the fixed usage soft-drain thresholds enters `DRAINING`, and fresh selection first narrows candidates to the best health tier. Consequently, a `burn_first` account imported at 98% usage is never selected while a healthy normal account exists, so its remaining quota cannot reach the unchanged 0% member-auth transition threshold.

Sticky and hard-continuity paths deliberately preserve ownership and must not gain this exception. The fresh and sticky paths currently share the budget-safe selector, so the behavior must be explicitly opt-in from the unbound path.

## Goals / Non-Goals

**Goals:**

- Admit a usage-only draining `burn_first` account for a fresh request with no existing account assignment.
- Require a separately selectable healthy fallback after model, status, quota, account-cap, and other existing eligibility filters.
- Reject error-induced draining accounts.
- Preserve recovery-probe priority and all sticky/continuity ownership behavior.

**Non-Goals:**

- Reallocate existing soft-sticky mappings.
- Change hard-continuity ownership.
- Change soft-drain thresholds or disable soft drain.
- Change the 0% member-auth automatic transition condition.
- Add API, database, dashboard, or configuration fields.

## Decisions

### Make the exception opt-in at the shared budget-safe selector

Add a default-off selector option that is enabled by `run_unbound_selection_path` and by soft-sticky selection only when no mapping exists yet. Evaluate the exception after bounded recovery-probe admission but before best-health-tier narrowing in routing strategies that already honor `burn_first`; explicit sequential/reset/single-account strategies keep their established ordering. Existing sticky-owner and hard-continuity callers retain the default and therefore preserve current ownership behavior.

Alternative considered: modify health-tier evaluation for every `burn_first` account. Rejected because it would also affect sticky paths and could drain the last usable account without proving a fallback.

### Derive usage-only draining from canonical health evaluation

A candidate qualifies only when it is active, `burn_first`, currently `DRAINING`, and canonical health evaluation shows quota usage would drain it while current recent-error evidence alone would not. This reuses the same thresholds and error window as the health state machine rather than duplicating constants.

Alternative considered: treat `error_count == 0` as sufficient. Rejected because recent error state and future health thresholds could diverge from that shortcut.

### Prove both candidate and fallback selectability

The selector first proves that at least one other healthy state can be selected with backoff fallback disabled and the request's existing eligibility context. It then selects only from qualifying usage-draining `burn_first` candidates. The usage-drain classifier excludes a candidate at 100%.

Alternative considered: check only for another healthy row. Rejected because a healthy row can still fail quota, status, or request-class eligibility.

## Risks / Trade-offs

- [A `burn_first` request can fail near quota exhaustion] → Require a separately selectable healthy fallback so ordinary retry/failover remains available.
- [A draining account with active error evidence could be misclassified] → Derive the cause through canonical health evaluation and exclude any currently error-induced drain.
- [Shared-selector changes could alter sticky routing] → Keep the option default-off and add regression coverage showing sticky callers do not opt in.
- [Recovery probes could be starved] → Run the existing recovery-probe decision before the new exception.

## Migration Plan

No data migration is required. Deploy the code change normally; rollback restores the prior fresh-selection ordering without changing persisted state.

## Open Questions

None.
