## ADDED Requirements

### Requirement: Outbound HTTP and WebSocket sessions transparently tunnel through a SOCKS proxy

The outbound HTTP and WebSocket clients MUST use a configured SOCKS proxy for all
upstream connections when any supported proxy environment variable carries a
SOCKS URL.
Configuring a SOCKS proxy MUST NOT require code changes — setting an environment
variable MUST be sufficient.

#### Scenario: SOCKS5 proxy is active — HTTP session uses ProxyConnector

- **GIVEN** `SOCKS_PROXY=socks5://gateway:1080` (or any equivalent env var below)
- **WHEN** the shared outbound HTTP client is initialised
- **THEN** the HTTP session uses a `ProxyConnector` built from that URL
- **AND** `trust_env=False` is passed to `aiohttp.ClientSession` to prevent double-proxying

#### Scenario: SOCKS5 proxy is active — WebSocket session routes through proxy when opt-in

- **GIVEN** a SOCKS URL is detected in the environment
- **AND** `upstream_websocket_trust_env=True` is configured
- **WHEN** the shared outbound WebSocket client is initialised
- **THEN** the WebSocket session uses a `ProxyConnector` built from the same SOCKS URL
- **AND** `trust_env=False` is passed to that session

#### Scenario: SOCKS5 proxy is active — WebSocket session connects directly when not opted in

- **GIVEN** a SOCKS URL is detected in the environment
- **AND** `upstream_websocket_trust_env` is not set to `True`
- **WHEN** the shared outbound WebSocket client is initialised
- **THEN** the WebSocket session uses a plain `TCPConnector` (unchanged behaviour)

#### Scenario: No SOCKS proxy configured — behaviour is identical to before

- **GIVEN** no SOCKS URL is present in any proxy environment variable
- **WHEN** the shared outbound HTTP client is initialised
- **THEN** both sessions use `aiohttp.TCPConnector` as before
- **AND** `trust_env` is passed unchanged per existing settings

### Requirement: SOCKS proxy URL detection follows a defined env var precedence

The service MUST probe the following environment variables in order and return the
first value that carries a SOCKS scheme:

1. `SOCKS_PROXY`
2. `socks_proxy`
3. `ALL_PROXY`
4. `HTTPS_PROXY`
5. `HTTP_PROXY`
6. `all_proxy`
7. `https_proxy`
8. `http_proxy`

Accepted input schemes: `socks5://`, `socks5h://`, `socks4://`, `socks4a://`.

Additional normalisation rules:
- Values MUST be stripped of leading/trailing whitespace before inspection.
- A bare `http://` scheme in `SOCKS_PROXY` or `socks_proxy` MUST be normalised
  to `socks5://` (accommodates misconfigured env vars while keeping the URL
  parseable by the configured proxy connector).
- `socks5h://` and `socks4a://` values MUST be normalised to `socks5://` and
  `socks4://` before connector construction because the configured proxy parser
  rejects the extended schemes.
- `HTTP_PROXY` and `http_proxy` MUST be skipped when `REQUEST_METHOD` is set in
  the environment (httpoxy / CGI security convention).

#### Scenario: Whitespace-padded value is accepted and returned stripped

- **GIVEN** `SOCKS_PROXY="  socks5://gateway:1080  "`
- **WHEN** the SOCKS URL is resolved
- **THEN** the returned URL is `socks5://gateway:1080` (no surrounding whitespace)

#### Scenario: Bare `http://` scheme in `SOCKS_PROXY` is normalised

- **GIVEN** `socks_proxy=http://gateway:1080`
- **WHEN** the SOCKS URL is resolved
- **THEN** the returned URL is `socks5://gateway:1080`

#### Scenario: Extended SOCKS schemes are normalised before connector use

- **GIVEN** `SOCKS_PROXY=socks5h://gateway:1080`
- **WHEN** the SOCKS URL is resolved
- **THEN** the returned URL is `socks5://gateway:1080`

#### Scenario: CGI environment skips `HTTP_PROXY`

- **GIVEN** `REQUEST_METHOD=GET` is set
- **AND** `HTTP_PROXY=socks5://gateway:1080` is the only SOCKS var
- **WHEN** the SOCKS URL is resolved
- **THEN** the result is `None` (variable is ignored)
