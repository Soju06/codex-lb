## 1. Contract

- [x] 1.1 Define verified OAuth identity, global policy, dual-caller Live routing, exact-owner sideband, persistence, and Settings requirements.
- [x] 1.2 Document the complete built-in OAuth and registered-Key Codex profiles, including both experimental realtime route overrides.

## 2. Implementation

- [x] 2.1 Add the typed OAuth identity resolver with bounded caching, singleflight, expiry limits, and credential-safe errors.
- [x] 2.2 Add the global policy ORM, reversible migration, repository, service, schemas, Dashboard API, and audit event.
- [x] 2.3 Add the Settings Live Voice card with a global enable switch, upstream Account multi-select, translations, and validation.
- [x] 2.4 Add one HTTP/WS caller resolver that preserves Proxy Key behavior and authorizes OAuth callers through the global pool.
- [x] 2.5 Preserve exact-owner binding across call creation and all supported sideband routes.

## 3. Verification and delivery

- [x] 3.1 Cover identity caching, policy validation, migration round-trip, HTTP/three-WS auth, owner continuity, nullable logging, and Key compatibility.
- [x] 3.2 Pass real local Codex Desktop normal conversation and audible Live Voice with both official OAuth and a registered Proxy API Key, plus policy revoke and logging/privacy acceptance.
- [x] 3.3 Sync stable requirements to main specs/context and pass the local verification gates.
