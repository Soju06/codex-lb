## Why

Sticky reallocation previously used one budget threshold for both primary and secondary usage pressure. Operators need to tune primary-window and secondary-window pressure independently so sticky sessions can move away from a weekly-drained account without changing the normal fresh-selection budget gate.

## What Changes

- Add dashboard settings for primary and secondary sticky reallocation thresholds while preserving the existing legacy threshold as a compatibility default.
- Apply the split thresholds only when evaluating sticky-session reallocation pressure.
- Keep fresh non-sticky selection on the existing primary/global budget-safety gate so weekly pressure does not unexpectedly override ordinary routing.

## Impact

- Sticky `codex_session`, `sticky_thread`, and `prompt_cache` mappings can rebind based on either configured primary or secondary pressure.
- Existing operators that leave the split values unset keep the prior single-threshold behavior.
- No change to sticky-session persistence keys or non-sticky account selection semantics.
