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

### Requirement: Responses dedicated ingress includes compact routes

When the Helm chart renders a dedicated Responses Ingress with exact path matching, it MUST route both standard Responses and compact Responses paths for public OpenAI-compatible routes and backend Codex routes.

#### Scenario: Dedicated Responses ingress renders all exact paths

- **WHEN** `ingress.responses.enabled=true`
- **AND** the chart renders exact paths for the Responses Ingress
- **THEN** it includes `/v1/responses`
- **AND** it includes `/v1/responses/compact`
- **AND** it includes `/backend-api/codex/responses`
- **AND** it includes `/backend-api/codex/responses/compact`
