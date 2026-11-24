## Why

Operators with many pooled accounts redeem banked reset credits one account at
a time from the Accounts page, or opt in to automatic redemption shortly
before expiry. There is no way to redeem every currently eligible credit in
one action (#2514).

## What Changes

- Add a **Redeem all resets (N)** action to the Accounts list, shown only to
  operators with account write access and only while at least one account can
  redeem a credit.
- The action opens a confirmation dialog listing each account that will redeem
  and how many credits it holds. Confirming redeems each listed account's own
  soonest-expiring credit through the existing per-account consume endpoint,
  one account at a time, and reports per-account success or failure.
- A failure on one account does not stop or hide the others.
- No backend endpoint, schema, setting or migration is added: every redeem
  reuses `POST /api/accounts/{id}/rate-limit-reset-credits/consume` and so the
  existing per-account serialization, durable idempotency ledger, cache
  invalidation, usage refresh and audit event.
- Out of scope: pooling credits across accounts (declined in #2289) and any
  change to the auto-redeem scheduler.

## Impact

- Frontend only (`frontend/src/features/accounts`).
- Spec: `rate-limit-reset-credits`.
