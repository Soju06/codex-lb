## ADDED Requirements

### Requirement: Upstream websocket handshakes auto-detect standard proxy environment variables

When operators don't explicitly configure `upstream_websocket_trust_env`, upstream websocket
handshakes MUST honor standard outbound proxy environment variables before connecting directly.
Explicit configuration MUST still override auto-detection.

#### Scenario: websocket handshakes honor env proxies by default

- **WHEN** `wss_proxy`, `ws_proxy`, `https_proxy`, `http_proxy`, or `all_proxy` is set
- **AND** `upstream_websocket_trust_env` is not explicitly configured
- **THEN** upstream websocket handshakes use the configured proxy instead of bypassing it

#### Scenario: explicit direct-connect override bypasses env proxies

- **WHEN** `upstream_websocket_trust_env=false`
- **AND** standard outbound proxy environment variables are set
- **THEN** upstream websocket handshakes connect directly without using those proxies
