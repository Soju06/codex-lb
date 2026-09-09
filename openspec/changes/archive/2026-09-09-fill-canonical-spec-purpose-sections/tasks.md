## 1. Purpose cleanup

- [x] 1.1 Confirm the 22 failing canonical capability IDs against a baseline strict validation run.
- [x] 1.2 Replace each placeholder Purpose with capability-specific non-normative prose derived from existing requirements.
- [x] 1.3 Verify no requirement, scenario, or unrelated file changed.

## 2. Verification

- [x] 2.1 Run normal and strict canonical OpenSpec validation and record counts.
- [x] 2.2 Run `git diff --check` and review the exact changed-file list.
- [x] 2.3 Verify and archive only after strict validation is green.
