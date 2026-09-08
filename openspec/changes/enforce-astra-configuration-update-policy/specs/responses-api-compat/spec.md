## ADDED Requirements

### Requirement: Astra configuration updates preserve compatible history

For subscription-backed Astra, the proxy SHALL preserve supported
`configuration_update` input items and normalize client-plane ultra to max
at subscription wire serialization. It SHALL reject unsupported reasoning
values and invalid adjacent updates before upstream work. Histories with
configuration updates SHALL reject automatic compaction, automatic
truncation, and the standalone compact endpoint. Normal request-level
reasoning SHALL remain unchanged by a valid input update. Explicit
terminal compaction_trigger items combined with configuration updates
SHALL remain on the Responses endpoint instead of being converted to a
standalone compact request. Astra-specific model restrictions SHALL NOT
be applied to externally configured model sources sharing the same model
ID.

Recorded subscription ownership of a previous response SHALL take precedence
over a configured source claiming the model when selecting the Astra schema
on both HTTP and WebSocket routes. Subscription-bound requests SHALL be
validated before upstream connection or send, using client-plane values.

#### Scenario: A valid update preserves the request cache prefix

- **WHEN** a request has request-level low reasoning and a high configuration_update between conversation messages
- **THEN** the forwarded request retains low at request level and high in that input item

#### Scenario: Explicit compaction retains configuration history

- **WHEN** a subscription Astra Responses request contains configuration updates and one terminal compaction_trigger
- **THEN** the proxy forwards the updates and trigger on the Responses path without calling the standalone compact endpoint

#### Scenario: A source with the same model name owns its model contract

- **GIVEN** a request routes to an externally configured model source named gpt-6-astra
- **WHEN** that source supports its own reasoning levels, logprobs or configuration update schema
- **THEN** subscription-specific Astra validation does not override that contract
- **AND** API-key reasoning policy remains enforced before source forwarding

#### Scenario: Source updates may leave reasoning unchanged

- **GIVEN** a reasoning-restricted API key routes to an external model source with an allowed request-level effort
- **WHEN** a source-specific configuration update omits reasoning.effort
- **THEN** the proxy SHALL preserve the update without requiring subscription-specific reasoning fields
- **AND** an explicit forbidden or non-string reasoning.effort SHALL still be rejected for the restricted key
- **AND** a source-owned WebSocket request SHALL retain its HTTP-transport fallback after the same policy check

#### Scenario: Source reasoning fields remain part of the reservation budget

- **GIVEN** a source-specific configuration update includes reasoning.effort and additional reasoning fields
- **WHEN** serialization prepares the request's API-key usage estimate
- **THEN** effort normalization SHALL preserve every other reasoning field for the existing input-budget calculation
- **AND** source forwarding SHALL retain the original configuration update
- **AND** overlapping requests SHALL remain subject to the existing reservation limit, including when the additional fields exhaust its remaining budget

#### Scenario: A subscription anchor overrides a source model contract

- **GIVEN** a configured source claims gpt-6-astra and a previous_response_id belongs to a recorded subscription account
- **WHEN** a WebSocket continuation contains source-only controls or an invalid configuration_update
- **THEN** canonical and equivalent Responses socket routes return the subscription invalid-request 400 before upstream connection or send
- **AND** valid subscription continuations retain their owner and API-key reasoning policy
- **AND** any preserved full-resend fallback is validated against the same subscription schema and refreshed API-key policy before it can be retained for replay
- **AND** source-owned continuations without a recorded subscription owner retain the HTTP-transport fallback rather than subscription schema errors

#### Scenario: Owner publication cannot change an already selected schema

- **GIVEN** a source-owned Astra continuation has no recorded subscription owner when preparation selects its schema
- **WHEN** another request publishes that owner before dispatch
- **THEN** the current WebSocket request SHALL retain the source HTTP-transport fallback
- **AND** a subsequent request SHALL resolve the published owner and apply subscription validation before forwarding

#### Scenario: Input normalization cannot create invalid adjacent updates

- **GIVEN** an anchored subscription Astra history contains configuration updates separated by a repeated side-effect tool call
- **WHEN** replay deduplication or subscription serialization removes the intervening items and leaves the updates adjacent
- **THEN** HTTP-bridge and WebSocket preparation SHALL reject the resulting payload before upstream connection or send
- **AND** a history whose updates remain separated after deduplication SHALL retain its supported ordering

#### Scenario: Late policy resets preserve client continuation state

- **GIVEN** a restricted-key Astra request receives a proxy-owned anchor after initial preparation
- **WHEN** the proxy adds the required leading configuration update
- **THEN** persisted input counts and fingerprints SHALL continue to describe the original client history
- **AND** a subsequent full resend SHALL retain prefix matching and session anchoring

#### Scenario: Configuration updates cannot use standalone compaction

- **WHEN** a compact request contains a configuration_update item
- **THEN** the proxy returns a compatible invalid-request error before upstream work
