# Proposal

## Why

Six Unicode digits pass TOTP normalization but make `hmac.compare_digest` raise
`TypeError`, turning invalid setup or verification input into HTTP 500.

## What Changes

- Retain only ASCII digits during existing code normalization.
- Cover malformed codes at the verifier and dashboard HTTP endpoints.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: non-ASCII digit codes receive the existing invalid-code response.

## Impact

The shared TOTP verifier and dashboard authentication tests. No schema, settings,
dependencies, or dashboard rendering changes.
