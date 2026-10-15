## 1. Restore Japanese SCIM copy

- [x] 1.1 Merge current main and reproduce the two reported failures with `bun run test src/i18n`.
- [x] 1.2 Translate all 23 missing keys, preserve existing entries and placeholders, and pass `bun run test src/i18n`.

## 2. Verify and record the integrated result

- [x] 2.1 Run the frontend suite, lint, and production build; verify the two previously fixed locale review findings with their regression tests.
- [x] 2.2 Sync the SCIM scenario and stable context to frontend-architecture, record results in `verification.md`, pass strict OpenSpec validation and `git diff origin/main --check`, and archive the verified change.
