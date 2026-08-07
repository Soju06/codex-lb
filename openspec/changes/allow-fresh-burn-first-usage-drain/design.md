## Context

Account runtime health is synchronized before routing-policy preference. An active account at or above the fixed usage soft-drain thresholds enters `DRAINING`, and fresh selection first narrows candidates to the best health tier. Consequently, a `burn_first` account imported at 98% usage is never selected while a healthy normal account exists, so its remaining quota cannot reach the unchanged 0% member-auth transition threshold.

Sticky and hard-continuity paths deliberately preserve ownership and must not gain this exception. The fresh and sticky paths currently share the budget-safe selector, so the behavior must be explicitly opt-in from the unbound path.

## Goals / Non-Goals

**Goals:**

- Admit a usage-only draining `burn_first` account for an owner-free fresh request.
- Require a separately selectable healthy fallback after model, status, quota, account-cap, and other existing eligibility filters.
- Reject error-induced draining accounts.
- Preserve recovery-probe priority and all sticky, continuity, and opportunistic routing behavior.
- Preserve one authoritative weighted draw and return that draw's actual winner.

**Non-Goals:**

- Reallocate existing soft-sticky mappings.
- Change hard-continuity ownership.
- Change soft-drain thresholds or disable soft drain.
- Change the 0% member-auth automatic transition condition.
- Add API, database, dashboard, or configuration fields.

## Decisions

### Make the exception opt-in at the shared budget-safe selector

Add a default-off selector option that is enabled by `run_unbound_selection_path` and by soft-sticky selection only when no mapping exists yet. Requests carrying an unresolved ownership requirement do not enable the option even when no owner row has resolved yet. Evaluate the exception after bounded recovery-probe admission but before best-health-tier narrowing in routing strategies that already honor `burn_first`; explicit sequential/reset/single-account strategies keep their established ordering. Existing sticky-owner and hard-continuity callers retain the default and therefore preserve current ownership behavior.

Alternative considered: modify health-tier evaluation for every `burn_first` account. Rejected because it would also affect sticky paths and could drain the last usable account without proving a fallback.

### Derive usage-only draining from canonical health evaluation

A candidate qualifies only when it is active, `burn_first`, currently `DRAINING`, and canonical health evaluation shows quota usage would drain it while current recent-error evidence alone would not. This reuses the same thresholds and error window as the health state machine rather than duplicating constants.

Alternative considered: treat `error_count == 0` as sufficient. Rejected because recent error state and future health thresholds could diverge from that shortcut.

### Prove fallback eligibility without discarding a routing decision

Only after finding a qualifying usage-draining candidate, the selector uses cloned healthy states and deterministic `single_account` selection to prove that at least one other healthy state is eligible with backoff fallback disabled and the request's existing eligibility context. It then makes one authoritative selection from cloned healthy fallbacks plus the qualifying candidates normalized only for health-tier comparison, and returns the corresponding original state whether the winner is draining or healthy. This avoids a discarded weighted draw, duplicate winner logs, or returning a different account from the one routing selected. The usage-drain classifier excludes a candidate at 100%.

Alternative considered: check only for another healthy row. Rejected because a healthy row can still fail quota, status, or request-class eligibility.

Failure to prove a separate healthy fallback does not remove any candidate from the original pool. The exception is skipped, then the pre-existing routing path remains authoritative. This preserves foreground last-resort availability and opportunistic emergency-floor behavior without admitting the candidate through the new exception.

## Risks / Trade-offs

- [A `burn_first` request can fail near quota exhaustion] → Require a separately selectable healthy fallback so ordinary retry/failover remains available.
- [A draining account with active error evidence could be misclassified] → Derive the cause through canonical health evaluation and exclude any currently error-induced drain.
- [Shared-selector changes could alter sticky routing] → Keep the option default-off and add regression coverage showing sticky callers do not opt in.
- [An unresolved owner-bearing request could be mistaken for owner-free traffic] → Propagate the continuity requirement through both unbound and sticky selection and keep the exception disabled.
- [A fallback probe could consume a weighted draw or emit a winner that is discarded] → Use a cloned deterministic eligibility probe, then return the original state corresponding to the single authoritative selection.
- [A failed fallback proof could suppress established last-resort or opportunistic routing] → Preserve the original pool and fall through to baseline routing unchanged.
- [Recovery probes could be starved] → Run the existing recovery-probe decision before the new exception.

## Migration Plan

No data migration is required. Deploy the code change normally; rollback restores the prior fresh-selection ordering without changing persisted state.

## Open Questions

None.
