# deployment-networking Specification

## Purpose

Define network exposure and policy contracts so chart deployments default to explicit, least-privilege connectivity.

## Requirements

### Requirement: NetworkPolicy ingress defaults fail closed

When the Helm chart enables `networkPolicy`, it MUST NOT open the main HTTP ingress port to every namespace by default. Namespace-scoped ingress access MUST be rendered only when an explicit allowlist selector is configured, or when the operator supplies an equivalent extra ingress rule.

#### Scenario: Empty ingress namespace selector does not create an allow-all rule

- **WHEN** `networkPolicy.enabled=true`
- **AND** `networkPolicy.ingressNSMatchLabels` is empty
- **THEN** the rendered NetworkPolicy does not include `namespaceSelector: {}`
- **AND** ingress remains deny-by-default unless the operator adds an explicit allow rule

### Requirement: Stock Docker networking explains network switching

The documented portable standalone Docker deployment MUST attach codex-lb to a user-defined bridge network, and stock Compose deployments MUST declare a user-defined default bridge. The documentation MUST state that Docker's embedded resolver can retain stale external forwarding servers across a host network change. It MUST provide a Linux host-network launch option for operators who require the container to use the host's live resolver. Stock configuration MUST NOT hard-code a public recursive DNS server.

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
- **AND** the documentation explains the loss of Docker network-namespace isolation

#### Scenario: Portable bridge limitations are explicit

- **WHEN** an operator reads the portable bridge instructions
- **THEN** the documentation does not claim that `127.0.0.11` guarantees forwarder refresh after switching networks
- **AND** it identifies host networking or a host-resolver bridge listener as the stronger Linux options
