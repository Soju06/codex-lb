## 1. Implementation

- [x] 1.1 Set `keepalive_timeout=90`, `ttl_dns_cache=300` on the shared HTTP and websocket-handshake `TCPConnector`s

## 2. Validation

- [x] 2.1 Connector-construction unit assertions updated; suite green; `ruff`/`ty`; `openspec validate --specs`
