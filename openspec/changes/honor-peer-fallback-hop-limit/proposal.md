## Why

`peer_fallback_max_hops` is exposed as a configurable hop limit, but the runtime currently treats any inbound peer fallback marker as terminal. That makes values above `1` ineffective and makes the setting's behavior misleading.

## What Changes

- Allow an inbound peer-forwarded request to fallback again while its recorded fallback depth is below `peer_fallback_max_hops`.
- Continue rejecting peer-forwarded requests once their depth reaches the configured limit.
- Keep the default single-hop behavior because `peer_fallback_max_hops` defaults to `1`.

## Impact

- Runtime proxy: peer fallback loop prevention now honors the configured hop limit.
- Tests: add coverage for below-limit forwarding and at-limit rejection.
