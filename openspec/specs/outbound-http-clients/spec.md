# Outbound HTTP Clients

## Purpose

Define how codex-lb performs outbound HTTP requests for upstream APIs and service metadata.

## Requirements

### Requirement: Outbound aiohttp clients honor environment proxy settings
Shared outbound `aiohttp` clients MUST honor `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` environment variables so operator-configured proxy routing applies consistently to upstream OAuth, proxy, and metadata calls.

#### Scenario: Service runs behind an operator-configured proxy
- **WHEN** codex-lb initializes the shared HTTP client or creates the Codex version GitHub client
- **THEN** each outbound `aiohttp.ClientSession` is created with environment proxy support enabled
