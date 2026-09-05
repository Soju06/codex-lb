## ADDED Requirements

### Requirement: Default Docker deployments preserve the host OAuth callback port

The documented portable Docker launch commands and shipped Docker Compose files MUST publish the dashboard and proxy port 2455 but MUST NOT publish host port 1455 by default. The container MAY continue to expose and listen on its internal callback port.

Docker account-setup guidance MUST provide a path that works without host port 1455 publication, and MUST explain that Codex Desktop uses the same host callback port. Any documented opt-in host publication MUST bind to loopback, MUST be presented only for a machine without another callback consumer, and MUST state that Docker reserves the port whenever the container is running. Upgrade guidance MUST explain that an existing container retains its published ports until it is recreated.

#### Scenario: Stock Docker launch keeps host port 1455 free

- **WHEN** an operator follows a portable `docker run` example or starts either shipped Compose file without customization
- **THEN** host port 2455 is published for the dashboard and proxy
- **AND** host port 1455 remains available to Codex Desktop or another local callback consumer

#### Scenario: Docker account setup without callback-port publication

- **GIVEN** a stock Docker deployment does not publish host port 1455
- **WHEN** an operator adds or reauthenticates a codex-lb account
- **THEN** the documentation directs them to device-code sign-in or the dashboard's manual browser-callback field

#### Scenario: Dedicated host opts into automatic browser callback

- **GIVEN** a machine does not run Codex Desktop or another host-port-1455 consumer
- **WHEN** an operator chooses the documented automatic browser-callback opt-in
- **THEN** the mapping binds host port 1455 only on loopback
- **AND** the documentation warns that the running container reserves that port even while no browser flow is active

#### Scenario: Existing mapped container is upgraded

- **GIVEN** an existing container was created with host port 1455 published
- **WHEN** the operator upgrades to a release with the safer default
- **THEN** the documentation tells them to recreate the container without that mapping while preserving its data volume
