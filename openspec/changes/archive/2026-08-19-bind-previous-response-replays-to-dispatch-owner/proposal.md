## Why

A pre-visible Responses retry can remove a `previous_response_id` anchor while
retaining account-scoped request material, exclude the original account, and
dispatch the retained payload to another account. Encrypted reasoning was
reproduced crossing accounts through the HTTP stream path; the same missing
dispatch provenance affects HTTP bridge and direct WebSocket retries.

PR #1818 fixed parameterless stale-response classification but intentionally
did not add payload-owner fencing. The remaining defect violates account
ownership even when session/file continuity and API-key settlement work as
designed.

## What Changes

- Classify exact-wire replay candidates with the canonical
  account-neutral-fresh-replay predicate.
- Bind every nonportable Responses payload to its first dispatch account.
- Merge payload ownership with previous-response and file ownership during
  every HTTP stream, HTTP bridge, and direct WebSocket selection.
- Fail closed rather than excluding the owner or moving retained
  account-scoped material during Trusted Access migration/degradation.
- Clear payload ownership only after verified anchor removal produces a
  canonical account-neutral fresh replay.
- Keep proxy-owned operation metadata on its current account unless a
  dedicated operation-rebind path replaces that identity before selection.
- Preserve existing file pinning, API-key settlement ordering, error
  classification, and raw error-envelope behavior.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: require account-bound retry payloads to remain on
  their dispatch owner across all Responses transports.

## Impact

- **Affected code:** Responses replay safety, HTTP streaming retries, HTTP
  bridge reconnects, and direct WebSocket account switching.
- **Affected tests:** proxy streaming utilities and WebSocket Responses
  integration tests.
- **API/schema changes:** none.
- **Configuration changes:** none.
- **Security impact:** prevents account-scoped request material from crossing
  account boundaries during internal retries.
