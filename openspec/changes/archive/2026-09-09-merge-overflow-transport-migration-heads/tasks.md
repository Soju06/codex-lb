## 1. Contract

- [x] 1.1 Record the two-parent forward merge and data-preservation boundary.

## 2. Implementation and proof

- [x] 2.1 Add the no-operation merge revision without editing either parent.
- [x] 2.2 Verify the sole graph head and populated upgrade paths from each
  parent and both parents, including original parent normalization behavior.
- [x] 2.3 Verify downgrade of the merge to either parent preserves both parent
  schemas and data, restores both revision stamps, and permits re-upgrade.
- [x] 2.4 Run migration policy, drift, relevant tests and strict OpenSpec checks.
- [x] 2.5 Complete independent Input and Standards review.
