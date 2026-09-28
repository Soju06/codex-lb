## Why

File upload registration returns 502 when its account-routed proxy connection fails before dispatch, even when another eligible account can complete the request. The files adapter drops typed transport provenance, bypassing the existing pre-visible unary failover policy.

## What Changes

- Preserve confirmed pre-dispatch routed transport provenance across the file client and service error adapters.
- Apply existing unary failover to movable file operations without relying on sanitized exception text.
- Preserve strict file-owner routing and reject replay after ambiguous dispatch, body-read failures, or TLS verification failures.
- Cover the externally failing files route with routed transport regressions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Clarify routed file transport provenance under the existing pre-visible unary refresh/connect failover requirement.

## Impact

File client error conversion and unary failover classification; no API shape, database schema, dependency, or configuration changes. File create/finalize request budgets and owner pins remain authoritative.
