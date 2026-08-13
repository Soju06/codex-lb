## Why

Codex clients configured with only the ordinary `codex-lb` provider cannot express a Trusted Access requirement before the first account-selection attempt. A later upstream `cyber_policy` result may arrive after `response.created`, when changing accounts or replaying the request is no longer safe.

## What Changes

- Publish a separate, explicitly selected Daybreak Blue Codex provider and profile that authenticates with a Codex LB API key and adds `X-Codex-LB-Required-Capability: trusted_cyber` to every provider request.
- Require a valid proxy API key for a direct Responses WebSocket request that carries the capability header even when global proxy API-key auth is disabled, without changing auth behavior for requests that omit the header.
- Authenticate and reject capability-bearing HTTP Responses and compact fallback requests before account selection or upstream dispatch, because current Codex clients do not expose a WebSocket-only provider control.
- Keep the existing ordinary `codex-lb` provider free of the capability carrier and preserve its current model and routing behavior.
- Select the existing `gpt-5.6-sol` model through the Daybreak profile; the profile name and authorized account surface, not a model alias alone, identify the intended use.
- Add inert regressions that load the published client configuration, exercise an installed Codex client in a network-isolated loopback harness, prove the Daybreak profile reaches capability ingress before first selection, and prove HTTP fallback cannot reach ordinary routing.
- Document that the profile narrows routing only to accounts already marked and independently approved for security work; it does not grant Trusted Access.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define the opt-in Codex provider/profile contract that carries trusted-cyber intent before the first Responses WebSocket routing decision without changing ordinary client traffic.

## Impact

- User-facing Codex client setup documentation and checked-in inert configuration examples.
- Focused installed-client, direct Responses WebSocket, and HTTP fallback coverage at the client-config-to-capability-ingress seam.
- No proxy selector changes, new settings, environment variables, dependencies, migrations, dashboard changes, or automatic modification of user configuration. Ordinary requests keep their existing authentication and routing behavior.
