## Why

An upstream safety-policy rejection describes the submitted request, not the
health of the selected account. Treating that deterministic rejection as a
transient account failure can push healthy shared accounts into backoff.

The HTTP Responses bridge also counts a pre-output terminal failure against
its retry circuit. Repeated policy-blocked requests can therefore cool down or
quarantine an otherwise healthy continuity key even though changing the
account or reconnecting the bridge cannot make the request acceptable.

## What Changes

- Recognize the upstream `misalignment_policy_violation` safety-block shape by
  its code, allowed HTTP status, and safety-system message.
- Keep that request-scoped rejection out of account health while preserving
  its non-retryable classification and client-visible failure.
- Keep the same terminal failure out of the HTTP bridge retry circuit and
  forward the original `response.failed` event unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: safety-policy request blocks are account-health neutral.
- `responses-api-compat`: HTTP bridge safety-policy terminal events remain
  client-visible without poisoning the bridge retry circuit.

## Impact

- Proxy failure classification, stream health handling, and HTTP bridge
  terminal settlement only.
- No schema, migration, setting, credential, routing-selection, or public API
  shape changes.
