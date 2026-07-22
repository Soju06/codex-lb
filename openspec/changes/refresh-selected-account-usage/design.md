## Context

`UsageRefreshScheduler` has two distinct scopes today. Its rotation is already account-at-a-time, but the read phase loads primary and secondary snapshots for every account before choosing the slot. After a successful write, it again loads fleet-wide snapshots, evaluates warm-up with every account, and gives recovery a fleet-wide repository. `UsageRepository.latest_by_account()` already supports `account_ids`, so no query or schema primitive is missing.

The scheduler must retain three existing invariants: the full eligible roster determines deterministic rotation, staggered-idle warm-up phases use a stable fleet cohort, and every upstream operation runs after the originating `AsyncSession` has closed. The newly merged #1376 Force Probe settlement is a separate, operator-driven path and remains unchanged.

## Goals / Non-Goals

**Goals:**

- Make recurring usage-history work proportional to one selected account per scheduler slice.
- Ensure usage refresh, warm-up candidate evaluation, and recoverable-status reconciliation cannot spill into an unrelated account during that slice.
- Preserve deterministic account rotation, staggered-idle warm-up phase calculation, credential-slot ownership checks, per-account failure isolation, and session lifecycle safety.
- Cover the scheduler through both focused tests and a real repository-backed integration path.

**Non-Goals:**

- Batch usage writes or commits; that is an independent transaction-focused improvement.
- Change retention, rollups, indexes, dashboard reads, Force Probe, fleet refresh, or request-time refresh behavior.
- Add settings, migrations, dependencies, APIs, or cross-account failover to background refresh.

## Decisions

### Select the slot before reading usage history

The scheduler will load the small account roster, choose one account, and only then request primary and secondary snapshots with `account_ids=[selected_account.id]`. This uses the repository's existing indexed account filter and avoids introducing another read API.

Alternative considered: keep fleet-wide snapshots and slice the returned dictionaries in memory. That preserves outputs but leaves the expensive database work from #708 untouched.

### Separate warm-up evaluation scope from its stagger cohort

After a successful selected-account refresh, the scheduler will reload current account rows so opt-in and status changes remain visible. `LimitWarmupService.run_after_usage_refresh()` will receive the selected current account as its evaluation set and the full current roster only as an optional stagger cohort. Warm-up attempts and sends are therefore possible only for the selected account, while `_staggered_idle_due()` retains the same phase calculation across all eligible warm-up accounts.

Alternative considered: pass only the selected account through the existing parameter. That would make every account appear to be slot zero in the rolling warm-up cycle and change prestart behavior. Passing the full roster as evaluation input was also rejected because it needlessly queries attempts and evaluates unrelated accounts.

### Filter recovery at the repository boundary

`reconcile_recoverable_account_statuses()` will derive recoverable candidate ids first and pass them to each primary, secondary, and monthly latest-usage lookup. The scheduler will call it with only the selected current account. This retains compare-and-set status writes while proving that unrelated rows cannot participate.

Alternative considered: filter only the returned dictionaries. That would still execute fleet-wide history lookups and would not address the performance failure mode.

### Preserve sequential ownership and session boundaries

The scheduler continues to await one `UsageUpdater.refresh_accounts([selected])` call per slice. It will not substitute another account on failure. Read sessions are closed and ORM rows detached before upstream usage or warm-up traffic begins; existing background repository adapters continue to create independent sessions for any concurrent warm-up tasks.

## Risks / Trade-offs

- [Risk] Separating warm-up candidates from the stagger cohort could accidentally shift phase ordering. → Keep the full refreshed roster as the cohort and add a regression that only the selected account is evaluated while all eligible account ids remain in phase calculation.
- [Risk] An account can be deleted or become ineligible during its upstream refresh. → Reload the selected account afterward; if it no longer exists, skip warm-up and recovery instead of falling back to another account.
- [Risk] A per-account refresh failure leaves that slot without new data. → Preserve current behavior: do not cross account boundaries in the same slice; the deterministic rotation advances normally on later slices.
- [Trade-off] The accounts table is still read for rotation and post-refresh warm-up cohort freshness. This bounded control-plane read is independent of `usage_history` size and avoids changing warm-up semantics.

## Migration Plan

No data or configuration migration is required. Deploy normally. Rollback restores fleet-wide scheduler reads without altering stored data.

## Open Questions

None.
