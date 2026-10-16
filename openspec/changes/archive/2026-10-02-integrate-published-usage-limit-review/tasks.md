- [x] 1. Merge the published PR and reconcile backend/frontend/spec conflicts while preserving local and published fixes.
- [x] 2. Converge migration heads and verify historical schemas and saved policy values from both lineages.
- [x] 3. Verify scalar and override admission races, empty-poll telemetry, errors, ownership, cancellation, and dashboard behavior.
- [x] 4. Run affected backend/frontend, lint, typing, migration, and strict OpenSpec checks; sync stable requirements and context.
- [x] 5. Archive verified integration, commit, push the existing PR branch normally, and confirm its published head.

- [x] 6. Resolve the current-head Docker security scan failure with a focused urllib3 patch upgrade and verify the locked installation.
