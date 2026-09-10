## ADDED Requirements

### Requirement: Routed aiohttp egress carries proxy credentials outside the proxy URL

When the Codex upstream client dispatches a routed HTTP request or WebSocket
connect through aiohttp, it MUST pass a credential-free proxy URL
(`scheme://host:port`) and MUST carry the endpoint username and password as a
`Proxy-Authorization` Basic header whose bytes are identical to the header
aiohttp derives from URL userinfo (latin1 encoding). The client MUST NOT place
proxy credentials in the aiohttp proxy URL. Because aiohttp forwards proxy
headers only on the CONNECT tunnel, a route whose ordered pool contains any
credentialed endpoint MUST fail closed for a non-TLS (`http`/`ws`) upstream
target before any connection is opened and ahead of every transport branch
(aiohttp, native egress, SOCKS), surfacing as a credential-free connect-phase
transport error, so a credential-free fallback endpoint cannot absorb the
misconfigured primary. Route resolution MUST fail closed for a proxy username
containing `:`. Native egress and SOCKS transports keep carrying credentials
through their existing URL and field inputs.

#### Scenario: Credentialed https endpoint uses Proxy-Authorization

- **GIVEN** a resolved `https` proxy endpoint with a username and password
- **WHEN** the Codex upstream client sends a routed request or opens a routed WebSocket through aiohttp
- **THEN** the aiohttp `proxy` argument contains no userinfo
- **AND** the CONNECT request carries a `Proxy-Authorization` header whose value is byte-identical to the userinfo-derived token
- **AND** the aiohttp connection-key repr and the proxy-error message text contain neither the password nor its Basic token
- **AND** the proxy-error repr, which carries the tunnel request headers, renders with `Basic [REDACTED]` through the log formatters (see `proxy-runtime-observability`)

#### Scenario: Credentialed route to a plaintext target fails closed

- **GIVEN** a resolved route whose primary proxy endpoint carries credentials and whose fallback does not
- **WHEN** the Codex upstream client is asked to reach an `http` or `ws` upstream URL, for an idempotent or non-idempotent request or a WebSocket open, whether or not a native egress helper is available
- **THEN** the client fails before dispatch with a credential-free connect-phase transport error
- **AND** no endpoint in the pool, including the credential-free fallback, receives the request on any transport

#### Scenario: Username with a colon is rejected at resolution

- **WHEN** a proxy endpoint username contains `:`
- **THEN** route resolution fails closed with reason `invalid_proxy_username`
