## 1. Regression coverage

- [x] 1.1 Prove an already-Free opted-in account with no previous attempt and
  a current-refresh zero-use monthly sample sends one monthly warm-up.
- [x] 1.2 Prove stale samples, partial usage, and any previous attempt remain
  ineligible for the initial path.
- [x] 1.3 Prove repeated/sliding refresh samples cannot create another initial
  warm-up.
- [x] 1.4 Prove normal and skipped attempts created earlier in the same refresh
  close the initial path.
- [x] 1.5 Prove concurrent initial claims with different sliding deadlines
  admit one attempt across processes.

## 2. Implementation

- [x] 2.1 Add the one-time initial Free monthly fallback without weakening
  ordinary reset confirmation or paid-to-Free detection.
- [x] 2.2 Reuse the existing monthly attempt identity and extend its atomic
  claim with an account-wide no-prior-attempt condition.

## 3. Verification

- [x] 3.1 Run focused limit-warm-up and atomic claim tests.
- [x] 3.2 Run Ruff, Ty, architecture/timing guards, strict OpenSpec validation,
  and diff hygiene checks.
- [x] 3.3 Reproduce mixed-version PostgreSQL claims for every warm-up window
  and preserve the existing advisory-lock protocol during rolling upgrades.
