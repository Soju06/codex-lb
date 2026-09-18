## ADDED Requirements

### Requirement: Steering instrumentation is deferred until needed
The proxy SHALL reuse its existing serialized Astra request rather than retain a second complete payload before steering. It SHALL derive retained configuration when steering or completed-parent retention needs it. The aiohttp adapter SHALL leave its private writer transport unchanged until a steering-sensitive explicit send requires handoff observation. If that observation cannot be installed, the proxy SHALL reject that send before dispatch without disrupting ordinary traffic or transferring its reservation to another request.

#### Scenario: Ordinary Astra traffic avoids steering instrumentation
- **WHEN** an Astra request is prepared and sent without a steering-sensitive continuation
- **THEN** it SHALL retain no additional full steering payload and SHALL NOT alter the aiohttp writer transport
- **AND** completed-parent retention SHALL preserve the settings required by later steering

#### Scenario: Unsupported transport instrumentation rejects only the affected send
- **GIVEN** an aiohttp writer no longer exposes the required transport interface
- **WHEN** a steering-sensitive explicit send requires dispatch observation
- **THEN** it SHALL fail before upstream dispatch
- **AND** ordinary sends SHALL remain usable and normal cleanup SHALL retain reservation ownership
