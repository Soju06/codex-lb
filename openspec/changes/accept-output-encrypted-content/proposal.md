## Why

Recent Codex Desktop requests can include `output.encrypted_content`, which the proxy currently rejects. Issues #2684 and #2748 document the incompatibility and a successful manual allowlist fix. The original fix was proposed in #2682.

## What Changes

- Accept `output.encrypted_content` in Responses include validation.
- Preserve existing validation and forwarding behavior.
- Add focused request-model regression coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: accept the encrypted-output include selector.

## Impact

One allowlist entry, request-model tests, and OpenSpec. No dependency, authentication, or routing changes.
