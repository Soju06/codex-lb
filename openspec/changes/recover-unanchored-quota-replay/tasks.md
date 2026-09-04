## 1. Replay eligibility

- [x] 1.1 Project response-owned bookkeeping from unanchored full-resend input.
- [x] 1.2 Require a self-contained account-neutral transcript with retained prior output and
  fresh follow-up input.
- [x] 1.3 Preserve every hard ownership and single-account routing boundary.

## 2. Failover behavior

- [x] 2.1 Clear the soft dispatch owner after a pre-visible quota rejection and reallocate
  prompt-cache affinity.
- [x] 2.2 Cover both HTTP-status and first-SSE-event quota failures.

## 3. Validation

- [x] 3.1 Add route-level success and fail-closed regression coverage.
- [x] 3.2 Run focused streaming, ownership, replay-safety, and lint validation.
- [ ] 3.3 Run strict OpenSpec validation.
