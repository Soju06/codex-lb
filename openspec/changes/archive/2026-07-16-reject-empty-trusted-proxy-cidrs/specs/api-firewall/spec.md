## ADDED Requirements

### Requirement: Enabled proxy-header trust requires source configuration

The application MUST fail settings validation when `firewall_trust_proxy_headers` is enabled and the normalized `firewall_trusted_proxy_cidrs` list is empty. The validation MUST apply independently of dashboard authentication mode and MUST identify the conflicting settings. An empty trusted-proxy CIDR list MUST remain valid while proxy-header trust is disabled.

#### Scenario: Enabled trust with empty CIDRs fails startup

- **WHEN** `firewall_trust_proxy_headers=true`
- **AND** `firewall_trusted_proxy_cidrs` is empty or contains only whitespace and delimiters
- **THEN** settings validation fails before the application starts
- **AND** the error identifies that enabled proxy-header trust requires at least one trusted-proxy CIDR

#### Scenario: Disabled trust permits an empty CIDR list

- **WHEN** `firewall_trust_proxy_headers=false`
- **AND** `firewall_trusted_proxy_cidrs` normalizes to an empty list
- **THEN** settings validation succeeds
- **AND** forwarded client-IP headers remain untrusted
