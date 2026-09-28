## Why

PR #2099's async replay support allows intervening turns before a delayed result. Once that result is stored, the outstanding-call map no longer retains the pair for shape validation. Malformed interleaved async pairs can therefore newly authorize owner-bound fresh reattachment.

## What Changes

Validate every async call and matching output in a stored prefix, including already-settled pairs, with the existing self-contained tool-item validator. Keep outstanding identities separate from validation evidence. Preserve synchronous prefix policy, account ownership, completed-assistant boundaries and suffix matching.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: validate settled asynchronous prefix history before durable replay classification.

## Impact

The shared prefix proof and focused unit/HTTP reattachment regressions only. No settings, schema, transport or recovery-policy expansion.
