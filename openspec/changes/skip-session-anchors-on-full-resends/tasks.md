## 1. Bridge behavior

- [x] 1.1 Exclude verified self-contained full resends from session-level previous-response anchor injection while preserving unsafe resend, durable, quarantine, and account-ownership rules.
- [x] 1.2 Add bridge and public-route regression coverage for safe and unsafe trimmable full resends.

## 2. Focused verification

- [x] 2.1 Run the full-resend, terminal-delivery, shared-future, warning-rate, and idle-sweep regression slices.
- [x] 2.2 Run the relevant HTTP bridge integration and migration checks without typechecking.
- [x] 2.3 Run Ruff, formatting, proxy architecture checks, and strict OpenSpec validation.

## 3. Candidate proof

- [x] 3.1 Build the source-based beta-4 candidate without copying older overlay files.
- [x] 3.2 Verify migration, representative multi-turn behavior, and rollback on an isolated clone before production handoff.
