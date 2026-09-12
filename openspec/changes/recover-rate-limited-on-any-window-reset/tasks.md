## 1. Evidence resolution

- [x] 1.1 Add `_RESET_EVIDENCE_WINDOWS` and `_normalized_usage_window`
- [x] 1.2 Rename `_MonthlyResetEvidence` to `_ResetEvidence` and expose its window
- [x] 1.3 Search every quota-window slot for the anchored post-block transition
- [x] 1.4 Drop the Free-plan gate from the anchored lookup

## 2. Recovery predicate

- [x] 2.1 Rename to `_confirmed_window_reset_recovery` and drop the plan gate
- [x] 2.2 Bind baseline, before, after, and latest to the anchored window
- [x] 2.3 Add `_sibling_window_blocks_recovery` with the elapsed-window exclusion
- [x] 2.4 Count a sibling only when it is live: the slot's plan capacity is not
      known to be zero, and its newest row carries the anchored window's own
      newest `recorded_at` (one fetch writes every reported window at once)

## 3. Consistent window snapshot

- [x] 3.1 Add `UsageRepository.latest_by_account_per_window` (one statement,
      latest row per account/slot)
- [x] 3.2 Read reconciliation's windows through it instead of one call per window

## 4. Warm-up isolation

- [x] 4.1 Substitute only monthly-slot evidence into the warm-up monthly pair

## 5. Coverage

- [x] 5.1 Unit: paid account recovers after an anchored weekly reset
- [x] 5.2 Unit: evidence from an unanchored window does not recover
- [x] 5.3 Unit: an elapsed exhausted sibling does not veto recovery
- [x] 5.4 Unit: the anchored lookup searches non-monthly slots
- [x] 5.5 Unit: retitle the Plus case to the sibling-exhaustion invariant
- [x] 5.6 Integration: scheduler recovers a Pro account after an early 7d reset
- [x] 5.8 Unit: a downgraded Free account recovers despite an obsolete paid
      `secondary` row
- [x] 5.9 Unit: a Free account whose live quota is outside `monthly` recovers
      despite a stale monthly row
- [x] 5.10 Unit: a sibling recorded by the same fetch still vetoes
- [x] 5.11 Unit: an unrecognized plan still honors a reported exhausted sibling
- [x] 5.12 Unit: a sibling lagging the anchored row at all is stale (pins the
      boundary against reintroducing a grace period)
- [x] 5.13 Unit: reconciliation issues one windows query, not one per window
- [x] 5.14 Integration: the per-window query returns every slot's latest row and
      scopes to the requested accounts and slots
