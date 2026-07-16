## ADDED Requirements

### Requirement: Stock Docker networking explains network switching

The documented portable standalone Docker deployment MUST attach codex-lb to a user-defined bridge network, and stock Compose deployments MUST declare a user-defined default bridge. The documentation MUST state that Docker's embedded resolver can retain stale external forwarding servers across a host network change. It MUST provide a Linux host-network launch option for operators whose host exposes a stable resolver address, and MUST state that a direct DHCP-provided resolver can still become stale in host-network mode. Stock configuration MUST NOT hard-code a public recursive DNS server.

#### Scenario: Standalone quick start uses a user-defined bridge

- **WHEN** an operator follows the documented standalone Docker quick start
- **THEN** the instructions create the codex-lb bridge idempotently
- **AND** start the container with that bridge selected by `--network`

#### Scenario: Compose uses a user-defined default bridge

- **WHEN** Docker Compose renders either stock Compose deployment
- **THEN** the server is attached to a user-defined default bridge
- **AND** the rendered service does not pin a public DNS server

#### Scenario: Linux network-switching launch uses the host resolver path

- **WHEN** a Linux operator selects the documented launch for switching Wi-Fi or other networks
- **THEN** the container uses `--network host`
- **AND** the command does not publish ports with `-p`
- **AND** the documentation requires a stable host resolver address and identifies `systemd-resolved` as the verified setup
- **AND** the documentation warns that a direct DHCP-provided resolver may still become stale
- **AND** the documentation explains the loss of Docker network-namespace isolation

#### Scenario: Portable bridge limitations are explicit

- **WHEN** an operator reads the portable bridge instructions
- **THEN** the documentation does not claim that `127.0.0.11` guarantees forwarder refresh after switching networks
- **AND** it identifies host networking or a host-resolver bridge listener as the stronger Linux options
