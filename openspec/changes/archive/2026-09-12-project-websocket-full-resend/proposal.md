## Why

A direct WebSocket follow-up can retain the complete transcript but fail quota recovery because historical response IDs and encrypted reasoning bind the saved resend to the exhausted account.

## What changes

Retain the original full resend for same-account retries. Build the existing account-neutral projection only at account-switch preparation, after verifying that size handling has not altered the input and that it retains the prior assistant reply. Preserve the client fingerprint only when installing that proven projection.

## Impact

Direct WebSocket replay preparation and regression tests. No settings, schema, or HTTP routing changes. This addresses part of #1707; selection-time owner loss remains outside this change.

Tool-output-only continuations remain outside this PR. Supporting them requires carrying and verifying the pending-call manifest as well as the input prefix; this patch only handles resends with a completed assistant reply. The helper can later join the shared relocation decision proposed in #2374. Changes to the same path in #2207 need to preserve these replay and fingerprint checks.
