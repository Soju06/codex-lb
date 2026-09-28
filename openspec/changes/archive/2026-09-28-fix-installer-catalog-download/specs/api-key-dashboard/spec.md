## ADDED Requirements

### Requirement: Preserve HTTPS in installer exports behind a proxy

The authenticated installer endpoint SHALL accept an optional `scheme=https` query parameter that upgrades the exported provider, root OpenAI, and catalog URLs to HTTPS while preserving the request base URL's authority and path. It MUST reject other scheme values and MUST preserve existing request-scheme behavior when omitted. HTTPS dashboard script fetches and copied terminal commands MUST include this hint; HTTP dashboard requests MUST omit it. The hint MUST NOT affect authentication, proxy trust, or credential scope.

#### Scenario: HTTPS dashboard behind an HTTP proxy

- **GIVEN** the public dashboard uses HTTPS but the application sees an HTTP request
- **WHEN** the user exports an installer or copies its terminal command for any supported platform
- **THEN** the export request includes `scheme=https`
- **AND** both client endpoints and the catalog URL use HTTPS at the original request authority

#### Scenario: Local HTTP setup and invalid hints

- **WHEN** an HTTP dashboard exports an installer without the hint
- **THEN** generated URLs preserve the request scheme
- **AND** a request with `scheme=http`, an arbitrary URL, or another unsupported scheme receives 422 without an installer

### Requirement: Identify installer catalog requests to edge filters

Every generated platform installer MUST use the explicit `codex-lb-installer/1.0` user agent when downloading the authorized catalog. A catalog HTTP 403 MUST report that access was forbidden and direct the user to check HTTPS and proxy or firewall rules, without claiming that the key is invalid. Download failures MUST NOT print credentials or response bodies, follow redirects, or replace existing client files.

#### Scenario: Edge rejects the generic runtime signature

- **GIVEN** an edge filter rejects generic Python or PowerShell signatures but accepts the explicit installer user agent
- **WHEN** a valid exported installer fetches the catalog
- **THEN** the request carries Bearer authorization, JSON acceptance, and the installer user agent and setup succeeds

#### Scenario: Access remains forbidden

- **WHEN** catalog download returns HTTP 403 with a response body containing sensitive content
- **THEN** setup stops with HTTPS and proxy or firewall guidance and the existing client files remain unchanged
- **AND** neither the body nor the credential appears in output
