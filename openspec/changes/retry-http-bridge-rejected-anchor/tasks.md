## 1. Spec

- [x] 1.1 Add HTTP bridge rejected-anchor retry requirements and negative controls.

## 2. Implementation

- [x] 2.1 Retry the cleared rejected anchor through the existing fresh-upstream replay helper.
- [x] 2.2 Preserve fail-closed behavior for unsafe replay, second rejection, and fence misses.

## 3. Verification

- [x] 3.1 Add unit coverage for recovered completion, unsafe replay, and no-loop behavior.
- [ ] 3.2 Run targeted tests, full HTTP bridge unit file, Ruff, and OpenSpec validation.
