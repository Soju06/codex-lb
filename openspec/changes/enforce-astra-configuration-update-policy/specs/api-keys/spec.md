## ADDED Requirements

### Requirement: Configuration updates obey API-key reasoning controls

The proxy SHALL apply allowed and enforced reasoning controls to
`configuration_update` input items as well as request-level reasoning. It
SHALL reject an update that violates those controls before forwarding it.
Historical supported updates SHALL preserve their order and request-level
cache prefix. A subscription Astra continuation using a response or
conversation anchor and a reasoning-restricted API key SHALL explicitly
establish an allowed effective configuration before processing new input,
so an unseen inherited configuration cannot bypass the current policy.
This requirement also applies to proxy-injected anchors; repeated
preparation SHALL be idempotent.

#### Scenario: An in-history update cannot evade allowed efforts

- **GIVEN** an API key allows only low reasoning
- **WHEN** a request contains configuration_update selecting high
- **THEN** the request is rejected before upstream work starts

#### Scenario: Enforcement conflicts are explicit

- **GIVEN** an API key enforces low reasoning
- **WHEN** a request contains configuration_update selecting high
- **THEN** the request is rejected instead of silently applying the conflicting update

#### Scenario: An anchored continuation cannot inherit an unauthorized effort

- **GIVEN** a previous response has retained high reasoning and the current API key allows or enforces low
- **WHEN** a subscription Astra continuation supplies low request-level reasoning and an anchor without a leading configuration update
- **THEN** the proxy establishes low using a leading configuration update before the new input
- **AND** request-level reasoning and existing input order are preserved

#### Scenario: Owner forwarding preserves client reasoning identity

- **WHEN** a restricted-key continuation selects ultra and is prepared and forwarded through an owner instance more than once
- **THEN** preparation retains exactly one leading configuration update selecting ultra and request-level ultra, without treating either value as max during API-key policy checks
- **AND** only final subscription wire serialization maps both ultra values to max

#### Scenario: Injected Ultra resets survive repeated anchor advances

- **GIVEN** a proxy-injected HTTP-bridge anchor for an Ultra-only key
- **WHEN** the same reconstructed request is prepared again after the first injection serialized as max
- **THEN** the client-plane Ultra identity is restored for policy checks
- **AND** the continuation is not rejected as max

#### Scenario: Non-leading updates keep client-plane efforts across repeated anchors

- **GIVEN** a turn-state request with a non-leading configuration_update selecting Ultra
- **WHEN** continuation preparation prepends a policy update and a later advance reconstructs the request
- **THEN** every configuration_update is restored to client-plane Ultra before policy checks
