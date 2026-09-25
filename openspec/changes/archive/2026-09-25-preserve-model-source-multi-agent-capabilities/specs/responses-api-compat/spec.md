# responses-api-compat Delta

## ADDED Requirements

### Requirement: Multi-agent namespace tools reach capable HTTP sources

When a source model declares a non-blank string `multi_agent_version`, the Responses request projection MUST preserve `type: namespace` tool declarations and matching tool choices while forwarding to that source. A source model without that declaration MUST continue to drop namespace tools unless it explicitly opts into `namespace` via `experimental_supported_tools`.

#### Scenario: Collaboration tools are forwarded to a v2 source

- **GIVEN** a source model declares `multi_agent_version=v2`
- **AND** a request contains a `type: namespace` collaboration tool
- **WHEN** codex-lb forwards the request over HTTP
- **THEN** the namespace tool remains in the upstream `tools` list

#### Scenario: Undeclared namespace tools remain filtered

- **GIVEN** a source model does not declare `multi_agent_version`
- **WHEN** a request contains a namespace tool
- **THEN** codex-lb omits that tool from the upstream request
