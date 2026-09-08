## Why

Docker reserves published host port 1455 for the lifetime of a running container, independently of the OAuth listener inside it. This can intercept another local application's login callback, as reported in #2076.

## What Changes

- **BREAKING**: Stop publishing host port 1455 in portable Docker examples and shipped Compose defaults.
- Keep device-code and manual-callback setup available, with explicit instructions and a dedicated-host opt-in mapping.
- Document the Windows port-forward helper limitation and existing-container recreation.
- Keep this deployment decision independent from the listener expiry fix in #2079.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: Define default host-port publication and supported account setup paths for Docker.

## Impact

Compose files, launch examples, deployment guidance, and port-contract tests. No application code, dashboard rendering, configuration key, or schema changes.

This is a draft product-default proposal. Automatic browser callback capture and the existing Windows helper cease to work with the proposed stock mappings; owner approval under PRINCIPLES P1 is required before accepting that tradeoff.
