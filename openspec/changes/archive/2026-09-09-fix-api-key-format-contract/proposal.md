## Why

The API-key specification and original design require generated credentials to
use `sk-clb-` followed by 48 lowercase hexadecimal characters. The current
generator uses `secrets.token_urlsafe(32)`, so every created or regenerated key
instead has a 43-character base64url suffix. Native authentication still works,
but strict consumers can reject the returned credential because it violates the
published contract.

## What Changes

- Generate new and regenerated API keys with `secrets.token_hex(24)`.
- Assert the complete generated-key format through service and API regression
  coverage instead of checking only the prefix.
- Preserve hash-based authentication for already-issued base64url keys.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Enforce the documented generated-key format for creation and
  regeneration while preserving existing-key authentication.

## Impact

The change is limited to the API-key plaintext generator, focused API-key
tests, and the API-key specification. It adds no API fields, settings,
dependencies, migrations, persistence changes, or frontend behavior.
