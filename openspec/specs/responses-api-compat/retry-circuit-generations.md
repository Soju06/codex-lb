# Retry-circuit generation context

The generation and claim-receipt requirements in [spec.md](spec.md) describe
the implementation in `fence-retry-circuit-admission-generation`. A successful
claim owns one immutable receipt; terminal cleanup uses that receipt so an
older request cannot clear a later replay's protection.

Local deadline arithmetic and scheduled cleanup use the service clock and
scheduler. Durable expiry and reclaim continue to use database statement time.
For example, a caller with one attempt left cannot spend a second attempt on
timeout reconciliation. A caller with remaining budget may reconcile once
after the first write settles cancellation.

If that first write committed but lost its result, the identical retry refuses.
One bounded lookup can recover the original receipt by its exact generation and
claim-start epoch, provided the database still considers it live. A competing,
expired, or unconfirmed receipt does not authorize dispatch.

The unmerged claim-marker migration follows main's
`20260909_070000_automation_run_claim_budget` revision, after report rollups,
dashboard-settings migrations and the overflow/transport repair from #2198. It adds
or removes only the nullable retry-circuit marker columns, preserving both
parents' schemas, overflow settings, explicit transport choices, model-source
pins, dashboard resilience, timeout, routing and overload overrides, populated
report history, fold watermarks, captured and legacy NULL automation budgets,
and retry generations. Marker rollback preserves captured automation budgets
and never drops report aggregates, even if raw history has already been pruned.
A live receipt still blocks rollback
before schema or version changes. Neither original parent nor their merge
revision, dashboard-settings migration, report-rollup migration or automation
budget migration is rewritten.

An ambiguous send or unconfirmed release can retain a receipt until expiry.
The stranded-receipt policy remains an explicit maintainer decision in the
[change design](../../changes/fence-retry-circuit-admission-generation/design.md#open-decision-stranded-claim-receipt-lockout).
Syncing these requirements does not resolve task 4.11, approve a shorter
lease or reclaim owner, or make `Retry-After` lease-aware. The change remains
active until that decision and the remaining delivery gates are satisfied.
