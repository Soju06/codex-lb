## 1. Account summary display expiry

- [x] 1.1 Null the primary display fields (remaining percent, remaining credits, reset, window duration) when the primary sample's reset has elapsed (long windows stay raw for weekly-pace consumers), after status derivation so displayed status stays aligned with routing.
- [x] 1.2 Remove the optimistic 100% primary-remaining default for accounts without a primary row.
- [x] 1.3 Coverage: expired primary samples display as absent; status badge unchanged; weekly-only behavior unchanged; live windows unaffected.

## 2. Pooled credit expiry

- [x] 2.1 Expire elapsed primary samples before pooling and return a null pooled primary percent when no live primary sample exists across the pooled accounts.
- [x] 2.2 Coverage: frozen expired rows no longer pool; all-expired/absent pools report null; live pools unchanged.

## 3. Validation

- [x] 3.1 Run mapper and api-keys suites; `openspec validate absent-window-display --strict`.
