## 1. Contract

- [x] 1.1 Specify the end-to-end failure-matrix gate and its intentional
  incomplete-transport policy.
- [x] 1.2 Specify version/weekly triggers, locking, atomic state, fast/full
  evidence labels, storage, and cleanup.

## 2. Implementation

- [x] 2.1 Add the privacy-safe failure matrix projection, baseline, CLI, and
  composite-gate integration.
- [x] 2.2 Add the version-aware lock-safe canary runner and controlled fast
  live-suite wrapper.
- [x] 2.3 Install the host-local user service/timer and document operation,
  manual invocation, full TLS attestation, and cleanup.

## 3. Verification

- [x] 3.1 Add failure gate and scheduler tests for pass, drift, missing
  scenarios, overlap, failed command, weekly due, and atomic state.
- [x] 3.2 Run focused/broad tests, lint, typing, strict OpenSpec validation,
  dry-run the scheduler, and execute the 0.151.0 version-change canary.
- [x] 3.3 Privacy scan retained outputs, archive the OpenSpec change, and
  verify the installed timer.
