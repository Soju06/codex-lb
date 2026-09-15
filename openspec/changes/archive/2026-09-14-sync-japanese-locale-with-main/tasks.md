## 1. Restore locale parity

- [x] 1.1 Merge current main into the locale branch and reproduce the reported missing/obsolete keys with the existing locale tests.
- [x] 1.2 Translate missing keys and remove obsolete keys; verify exact key equality, nonempty Japanese strings, interpolation, and markup with `src/i18n/index.test.ts`.

## 2. Verify the integrated frontend

- [x] 2.1 Add representative company sign-in, pending-account, and rename browser checks; run them and inspect English/Japanese screenshots.
- [x] 2.2 Run `bun run test`, frontend lint, and the production build against the merged tree; record the results in `verification.md`.
- [x] 2.3 Sync the frontend capability and stable context, run strict OpenSpec validation and `git diff --check`, and archive the verified change.
