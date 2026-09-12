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
      known to be zero, and its newest row does not lag the anchored window's

## 3. Warm-up isolation

- [x] 3.1 Substitute only monthly-slot evidence into the warm-up monthly pair

## 4. Coverage

- [x] 4.1 Unit: paid account recovers after an anchored weekly reset
- [x] 4.2 Unit: evidence from an unanchored window does not recover
- [x] 4.3 Unit: an elapsed exhausted sibling does not veto recovery
- [x] 4.4 Unit: the anchored lookup searches non-monthly slots
- [x] 4.5 Unit: retitle the Plus case to the sibling-exhaustion invariant
- [x] 4.6 Integration: scheduler recovers a Pro account after an early 7d reset
- [x] 4.8 Unit: a downgraded Free account recovers despite an obsolete paid
      `secondary` row
- [x] 4.9 Unit: a Free account whose live quota is outside `monthly` recovers
      despite a stale monthly row
- [x] 4.10 Unit: a sibling recorded by the same fetch still vetoes
- [x] 4.11 Unit: an unrecognized plan still honors a reported exhausted sibling
