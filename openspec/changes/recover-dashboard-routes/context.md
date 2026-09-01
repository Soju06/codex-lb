# Dashboard route recovery context

## Purpose

Keep shell context and native recovery controls visible when a dashboard route
is unknown, pending, or fails to load.

## Decision

The wildcard route remains under `AppLayout`. A pathname-keyed React error
boundary surrounds only the lazy outlet and tracks the complete React Router
location as a reset identity. Location changes reset only an already-failed
boundary, so healthy query/hash updates preserve outlet state and focus.
Pending chunks use the existing SpinnerBlock. Rejected lazy imports use full
reload because React caches the rejected promise; Dashboard navigation creates
a new router location identity even when its URL is unchanged.

## Constraints

- Preserve route-level code splitting and shell landmarks.
- No dependency, global error system, API change, or navigation item.
- No fixed sleeps or polling in tests.
- Reuse existing Button, SpinnerBlock, icons, and semantic tokens.

## Example

If the Accounts chunk is unavailable, `/accounts` keeps header/main/status,
announces a route-load error, and offers reload or Dashboard. An unknown
bookmark shows Not Found inside the same shell.
