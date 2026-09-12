## Tasks

- [x] Update shared usable-credit semantics so `credits_has = true` without unlimited credits or a positive balance does not override exhausted long-window usage.
- [x] Reuse the shared rule in account-summary/effective-status mapping.
- [x] Add quota and mapper regressions for missing balance, zero balance, positive balance, unlimited credits, and primary-window precedence.
- [x] Verify failure-classification evidence for `Stream must be set to true` and leave classification unchanged unless quota evidence is present.
- [x] Run targeted tests, ruff, and strict OpenSpec validation.
