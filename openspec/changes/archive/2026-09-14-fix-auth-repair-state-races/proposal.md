## Why

A routing snapshot that predates a committed rejection can suppress its local mark. Repairing rejected credentials can also leave a live cooldown hidden behind ACTIVE status. Both violate the account-routing contract under concurrent activity.

## What Changes

- Fence local rejection marks only against explicit same-account repairs, not snapshot rebuilds.
- Keep unexpired reset-based cooldowns effective after credential repair.
- Reuse existing encryptors in the touched credential consumers where their owners already provide one.
- Reproduce the races at the bridge reuse and account selection boundaries; verify the exact PR head and the CI failure separately.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `account-routing`: Preserve rejection evidence across stale snapshot publication and cooldown eligibility across token repair.

## Impact

Routing availability cache, guarded account token rotation, existing credential consumers, and focused regression suites. No schema, dependency, configuration, deployment, or external API changes. PR splitting and competing PR disposition remain maintainer decisions.
