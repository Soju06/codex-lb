## Why

Codex clients configured with only the ordinary `codex-lb` provider cannot express a Trusted Access requirement before the first account-selection attempt. A later upstream `cyber_policy` result may arrive after `response.created`, when changing accounts or replaying the request is no longer safe.

## What Changes

- Publish a separate, explicitly selected Daybreak Blue Codex provider and profile that authenticates with a Codex LB API key and adds `X-Codex-LB-Required-Capability: trusted_cyber` to every provider request.
- Keep the existing ordinary `codex-lb` provider free of the capability carrier and preserve its current model and routing behavior.
- Select the existing `gpt-5.6-sol` model through the Daybreak profile; the profile name and authorized account surface, not a model alias alone, identify the intended use.
- Add an inert integration regression that loads the published client configuration and proves the Daybreak profile reaches capability ingress before first selection, while the ordinary provider remains unconstrained.
- Document that the profile narrows routing only to accounts already marked and independently approved for security work; it does not grant Trusted Access.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define the opt-in Codex provider/profile contract that carries trusted-cyber intent before the first Responses WebSocket routing decision without changing ordinary client traffic.

## Impact

- User-facing Codex client setup documentation and checked-in inert configuration examples.
- Focused direct Responses WebSocket integration coverage at the client-config-to-capability-ingress seam.
- No proxy selector changes, new settings, environment variables, dependencies, migrations, dashboard changes, or automatic modification of user configuration.
