## Context

See proposal.md. Fernet ciphertext is randomized. Existing rejection persistence also compares status fields, while warmup and automation bypass normal selection.

## Goals / Non-Goals

Keep proven rejection tied to credential identity across refresh and concurrent health writes. Retain operator decisions and warning-only eligibility. No new schema or configuration; no unrelated routing redesign.

## Decisions

- Compare decrypted access-token material within guarded repository rotation; fence the final write against the observed ciphertext as well as the mandatory refresh ciphertext. Ciphertext inequality alone cannot establish repair.
- On a rejection CAS miss, re-read current state and retry only for the same credential generation. The retry updates only status and rejection reason, atomically guarding credentials, deletion, pause, and deactivation. It leaves cooldown columns untouched, so repeated health writes cannot exhaust the retry. Same-value ciphertext churn has three bounded comparison attempts.
- Reuse the canonical reason/expiry gate for direct consumers instead of duplicating routing policy.

## Risks / Trade-offs

- Concurrent replacement between read and write: retain exact generation guards and test both orderings.
- Undecryptable access material: do not claim a successful repair based on an unverifiable comparison.
- Example: re-encrypting access token A while rotating refresh token R1 to R2 keeps A rejected; replacing A with B can clear its rejection.
