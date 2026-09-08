## Why

An upstream safety-policy rejection describes the submitted request, not the
health of the selected account. Treating that deterministic rejection as a
transient account failure can push healthy shared accounts into backoff.

## What Changes

- Recognize the upstream `misalignment_policy_violation` safety-block shape by
  its code, allowed HTTP status, and safety-system message.
- Keep that request-scoped rejection out of account health while preserving
  its non-retryable classification and client-visible failure.

The message prefix is taken from the observed upstream safety-policy rejection
payload; the specific `misalignment_policy_violation` code remains mandatory
so unrelated wording or errors fail closed.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: safety-policy request blocks are account-health neutral.

## Impact

- Proxy failure classification and stream health handling only.
- No schema, migration, setting, credential, routing-selection, or public API
  shape changes.
