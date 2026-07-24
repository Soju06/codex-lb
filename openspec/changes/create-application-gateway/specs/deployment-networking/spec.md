## ADDED Requirements

### Requirement: Helm chart can create an application-specific Gateway

The Helm chart MUST allow operators to render a Gateway API `Gateway`
dedicated to the release in the release namespace instead of attaching to a
pre-existing shared Gateway. The mode MUST be optional and default off,
preserving the existing `gatewayApi.parentRefs` attachment. When enabled, the
chart MUST require an operator-supplied GatewayClass name, MUST default the
Gateway to a single HTTP listener on port 80 while honoring operator-defined
listeners verbatim, and MUST attach the chart-managed HTTPRoute to the
chart-managed Gateway while ignoring `gatewayApi.parentRefs`.

#### Scenario: Chart-managed Gateway with default listener

- **GIVEN** `gatewayApi.enabled=true`
- **AND** `gatewayApi.gateway.create=true` with a GatewayClass name
- **WHEN** the chart renders its Gateway API resources
- **THEN** a Gateway named after the release renders in the release namespace
  with the configured GatewayClass and one HTTP listener on port 80
- **AND** the HTTPRoute's only parent reference is the chart-managed Gateway

#### Scenario: Operator-defined listeners

- **GIVEN** `gatewayApi.gateway.create=true` with a GatewayClass name
- **AND** `gatewayApi.gateway.listeners` contains an HTTPS listener with TLS
  configuration
- **WHEN** the chart renders the Gateway
- **THEN** the configured listeners replace the default HTTP listener verbatim

#### Scenario: Missing GatewayClass name fails rendering

- **GIVEN** `gatewayApi.gateway.create=true`
- **AND** `gatewayApi.gateway.gatewayClassName` is empty
- **WHEN** the chart renders
- **THEN** rendering fails with an error naming
  `gatewayApi.gateway.gatewayClassName`

#### Scenario: Default configuration keeps existing Gateway attachment

- **GIVEN** `gatewayApi.enabled=true`
- **AND** `gatewayApi.gateway.create` is unset
- **WHEN** the chart renders its Gateway API resources
- **THEN** no Gateway resource renders
- **AND** the HTTPRoute attaches to the operator-supplied
  `gatewayApi.parentRefs`
