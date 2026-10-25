## Why
Refresh-singleflight completion and successful OAuth callback cleanup spawn tasks without retaining them at the spawning owner. Make their lifetime explicit rather than depend on whatever awaitable keeps a detached task reachable. This is preventive robustness, not a claim that garbage collection caused a production incident.

## What Changes
- Retain refresh completion tasks in their singleflight owner until done.
- Retain callback idle-stop tasks in their OAuth state store until done.
- Release completed references and consume unexpected idle-stop failures without logging credentials.
- Preserve refresh singleflight, cooldown, cancellation and callback response behavior.

## Capabilities
### Modified Capabilities
- `usage-refresh-policy`: ownership of refresh completion tasks.
- `replica-operations`: ownership of detached callback listener cleanup.

## Impact
Two runtime modules and lifecycle tests. No settings, migrations, routing, UI or credential changes. Existing dependency security updates are a separate prerequisite only.
