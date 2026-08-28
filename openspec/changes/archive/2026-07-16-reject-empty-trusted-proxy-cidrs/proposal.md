## Why

`firewall_trust_proxy_headers=true` is currently accepted after `firewall_trusted_proxy_cidrs` normalizes to an empty list, although no socket source can then be authorized to supply forwarded identity. The contradictory configuration silently changes locality behavior while failing to provide the documented proxy trust, so startup must reject it instead of running with operator intent unmet.

## What Changes

- Require at least one normalized trusted-proxy CIDR whenever proxy-header trust is enabled, independent of dashboard authentication mode.
- Preserve an empty trusted-proxy list when proxy-header trust is disabled.
- Move the existing trusted-header dashboard check under the shared proxy-trust invariant rather than maintaining a narrower duplicate.
- Add settings regressions for empty and whitespace-only CIDR input.
- **BREAKING (invalid configuration only):** deployments that explicitly enable proxy-header trust with no usable trusted CIDR will fail startup until they configure a CIDR or disable header trust.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `api-firewall`: Define the startup invariant that enabled proxy-header trust requires a non-empty normalized trusted-proxy CIDR list.

## Impact

- Affected code: `app/core/config/settings.py` cross-field validation.
- Affected tests: firewall settings validation.
- Existing documented reverse-proxy examples already configure both settings, so no user-documentation correction or new setting is required.