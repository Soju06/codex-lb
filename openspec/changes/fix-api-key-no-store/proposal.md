## Why

API-key creation and regeneration return a plain key available only once, but
those responses do not instruct clients/intermediaries not to store it.

## What Changes

- Apply the existing credential-response cache policy to both create URLs.
- Apply the same policy to regeneration.
- Preserve payloads, authorization, errors, persistence, and secret-free logs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: one-time plain-key responses prevent storage.

## Impact

API-key route headers and focused integration tests only. No schema, database,
setting, dependency, frontend, or generation change.
