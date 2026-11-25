## Why

Issue #2273 identifies an HTTP-bridge terminal classification mismatch. A reason-only `stream_incomplete` skips circuit accounting that the same explicit response error receives.

## What Changes

- Classify that exact incomplete reason through the existing circuit recorder when no explicit response error exists.
- Preserve neutral reasons, error precedence, logs, payloads and account-health treatment.
- Prove the change through registered readers using only current-main APIs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: reason-only incomplete terminal circuit accounting.

## Impact

One HTTP-bridge terminal classifier and its regression/spec coverage. No settings, schema, dependencies or UI changes. Independent of #1962 probe ownership, #2272 policies and their unmerged helpers.
