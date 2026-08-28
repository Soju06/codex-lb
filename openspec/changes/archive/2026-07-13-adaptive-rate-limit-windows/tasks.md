## 1. Selection-state expiry

- [x] 1.1 Generalize the elapsed-reset zeroing in `_state_from_account` from `used_percent >= 100` to any used percentage, keeping the post-remap position and locals-only mutation.
- [x] 1.2 Unit coverage: stale sub-100% primary row with elapsed reset no longer drives the drain tier or budget pressure; RATE_LIMITED account with an elapsed stale row still recovers; weekly-primary remap unaffected.

## 2. Blocked-status recovery evidence

- [x] 2.1 Generalize `_rate_limited_freshness_entry` to return the most recently recorded main-window row (keeping monthly precedence for monthly-capacity plans) so a post-block refresh without a primary window still proves recovery.
- [x] 2.2 Unit coverage: background recovery unpins a rate-limited account whose only post-block row is a fresh weekly row; ties still prefer the primary row; stale pre-block evidence still pins.

## 3. Updater freshness across windows

- [x] 3.1 Make the elapsed-reset staleness clause superseded by a strictly newer sibling-window row whose own freshness passes.
- [x] 3.2 Unit coverage: elapsed primary + newer fresh secondary row skips the upstream fetch; elapsed primary with no newer sibling still fetches.

## 4. Aggregated rate-limit surfaces

- [x] 4.1 Add a core helper that maps usage window rows with elapsed `reset_at` to expired samples (`0.0` used, no reset) and apply it in `_compute_rate_limit_headers` and `get_rate_limit_payload` after weekly-only normalization.
- [x] 4.2 Unit coverage: pooled headers report expired primary rows as 0% without a past reset-at; `limit_reached` is false when only elapsed samples report 100%.

## 5. Validation

- [x] 5.1 Run targeted unit and integration suites for the load balancer, usage updater, and proxy rate-limit surfaces.
- [x] 5.2 Validate the OpenSpec change with `openspec validate adaptive-rate-limit-windows --strict`.
