## 1. Reset Evidence

- [x] 1.1 Use the account/window/time-scoped usage-history lookup to require a monthly baseline recorded strictly after the block and matching the blocked deadline within tolerance.
- [x] 1.2 Resolve one canonical monthly reset-evidence tuple from an anchored current refresh pair or by scanning only adjacent persisted pairs at or after the matching baseline with the existing temporal reset predicate, including fallback from an unanchored current transition.
- [x] 1.3 Feed the resolved tuple to both blocked-status recovery and selected-window warm-up without widening the scheduler's selected-account scope.

## 2. Safe Status Recovery

- [x] 2.1 Add the Free monthly early-recovery predicate with the future-marker, matching-baseline, 30-second floor, post-block timestamp, and current availability gates.
- [x] 2.2 Run marker-guarded recovery before warm-up, clearing the reason and both block markers only when the status/reason/reset/blocked compare-and-set succeeds.
- [x] 2.3 Preserve ordinary recovery for elapsed cooldowns and reject early recovery for generic Retry-After, mismatched or jitter-only transitions, exhausted latest usage, non-Free accounts, and unsafe statuses.

## 3. Active-Only Warm-up

- [x] 3.1 Make reset-confirmed candidates eligible after every real selected-window reset regardless of previous usage or the legacy exhaustion-threshold setting while retaining post-reset availability, opt-in, and jitter gates.
- [x] 3.2 Restrict both warm-up candidate evaluation and the sender's fresh preflight to `active` accounts so a failed CAS or later re-block prevents upstream traffic.
- [x] 3.3 Reuse the recovered monthly reset tuple with the existing atomic account/window/reset attempt claim so restart recovery cannot duplicate warm-up.
- [x] 3.4 Keep `limit_warmup_cooldown_seconds` scoped to staggered idle candidates and record the follow-up removal plan for the now-unused exhaustion-threshold setting.

## 4. Regression Coverage

- [x] 4.1 Add a scheduler regression for a Free account stuck behind a future legacy deadline that recovers and warms after a confirmed monthly reset.
- [x] 4.2 Cover recovery from persisted transition history after restart, including an ineligible matching row exactly at `blocked_at` and an unanchored current transition before fallback to a later valid baseline, and prove the same monthly reset tuple is deduplicated.
- [x] 4.3 Cover the 30-second floor, missing or mismatched markers, stale/pre-block evidence, timestamp jitter, exhausted after/latest usage, generic Retry-After cooldown, and a Plus account with primary usage at `100%`.
- [x] 4.4 Cover compare-and-set contention and re-block-after-candidate races, proving neither stale recovery nor warm-up traffic occurs.
- [x] 4.5 Cover active-only warm-up plus a non-exhausted-to-available real reset to prove prior exhaustion is no longer required.

## 5. Verification

- [x] 5.1 Run focused scheduler, usage-repository, recoverable-status, and limit-warm-up unit/integration tests.
- [x] 5.2 Run the proportional regression suite plus Ruff, formatting, type checking, and `git diff --check`.
- [x] 5.3 Validate OpenSpec strictly and semantically verify every changed requirement and scenario against implementation and tests.
