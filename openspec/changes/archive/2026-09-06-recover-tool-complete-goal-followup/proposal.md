## Why

A client can resend a complete response's tool calls and matching results, then
append a user instruction when continuing a stopped goal. The durable pending
call manifest proves that no parallel call was omitted, but the existing proof
rejects every user follow-up after that complete batch. The HTTP bridge can then
inject its old response anchor and fail on an unavailable owner, despite having
a replayable full request. This is a bounded part of issue #1707, not a claim
that opaque compaction checkpoints can move between accounts.

## What Changes

- Accept a trailing self-contained user-input suffix after exact settlement of
  the durable prior-response tool manifest.
- Keep all retained calls, results, and fresh input in the replay body.
- Preserve existing durable-prefix, account-neutrality, file ownership, account
  scope, and pre-dispatch recovery checks.
- Preserve the tighter existing developer-interleave contract; this change does
  not combine that exception with a new trailing input suffix.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: full-resend context proof after a complete tool batch.

## Impact

One replay-proof helper and focused helper/HTTP-bridge regression tests. No new
settings, schema, migrations, or dashboard changes. No production deployment.

