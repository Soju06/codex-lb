# Restore dashboard control for quota-exceeded accounts

## Why

An operator can successfully use an account through Codex CLI while the dashboard still reports `quota_exceeded`. For weekly-only Professional accounts, the upstream usage API reports the weekly window in its primary slot. The dashboard recognizes it, but usage refresh recovery expects a secondary window and leaves the stale quota block in place. The dashboard also hides Resume in this state.

## What Changes

- Show the existing Resume action for a quota-exceeded account. It invokes the existing account reactivation endpoint and clears the persisted block for an operator retry.
- Recover quota-exceeded weekly-only accounts when fresh upstream weekly usage shows available quota, including 97% remaining.
- Clear a replica's stale quota cooldown after persisted recovery and fresh post-block weekly usage, so active reserve accounts are selectable even while other accounts have exhausted their short window.
- Preserve automatic quota evidence checks: a new upstream rejection can mark the account exhausted again.

## Impact

- Affected capability: account-routing.
- Affected code: dashboard account actions and regression test.
