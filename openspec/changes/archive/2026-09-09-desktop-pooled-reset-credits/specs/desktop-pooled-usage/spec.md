## MODIFIED Requirements

### Requirement: Account-owned metadata survives quota composition

The Desktop response MUST retain the original upstream caller envelope, including identity, plan, credits, spend controls, billing, reset credits and unknown account fields. The reset-credit field MAY instead use the explicitly enabled Desktop reset pool governed by `desktop-pooled-reset-credits`; when pooling is disabled it MUST remain original-account owned. It MUST replace only the canonical quota and additional quota for which equivalent fresh pooled evidence exists. Model-specific quota matching MUST match both normalized limit name and metered feature. Additional percentages MUST use equal-plan contributors with equal window durations, or a capacity-independent equal percentage across plans; otherwise the projection MUST return `pooled_usage_unavailable`. Unmatched same-named buckets MUST NOT be duplicated with an available bucket; unmatched original limits and reserve-model metadata MUST remain conservative. If the pooled main quota is available, the response MUST remove a known superseded `rate_limit_reached` marker and quota-only exhausted/reserve banners. It MUST preserve unknown restriction markers and account-owned spending restrictions. The response MUST NOT substitute another account's plan, credits or identity.

#### Scenario: Account plan and balances differ from the pool
- **WHEN** a Plus caller uses a pool that also contains Pro accounts and reset pooling is disabled
- **THEN** the response retains the caller's plan, identifiers, credit balance and reset credits while returning pooled quota windows

#### Scenario: Luna reserve is superseded by genuine pool quota
- **WHEN** the original response contains known quota-exhaustion state and the main pool quota is available
- **THEN** the response removes only that superseded quota restriction and retains account-owned restrictions and reserve metadata

#### Scenario: Additional model quota remains exhausted
- **WHEN** fresh equivalent pooled additional quota is exhausted or no equivalent pool evidence exists
- **THEN** the response does not claim that model is available merely because main pooled quota is available

#### Scenario: Main and model capacity are on different accounts
- **WHEN** one account has main quota but its model quota is exhausted and another has model quota but its main quota is exhausted
- **THEN** the model-specific pooled limit reports unavailable

### Requirement: Optional Desktop relay preserves caller traffic

`codex-lb desktop-relay` MUST support a standalone loopback listener on port 8000. The normal LB server MUST leave the relay disabled by default and MAY own the same relay through `CODEX_LB_DESKTOP_RELAY_MODE=loopback|container`. Loopback mode MUST bind only IPv4 and IPv6 loopback. Container mode MUST bind container ingress on port 8000 and MUST be documented with host publication restricted to IPv4 and IPv6 loopback. Embedded mode MUST use the normal server's HTTP loopback origin and configured port, reject unsupported TLS, IPv6-only or nonloopback-reachable listeners and invalid or colliding ports, and fail startup if relay startup fails. Its lifecycle MUST close relay listeners, active requests, WebSocket pumps and upstream sessions before shared LB resources are disposed, including partial startup failure. `GET /backend-api/wham/usage` and its trailing-slash alias MUST route to the configured local LB's strict Desktop quota endpoint. The native reset list and consume paths MAY route to the local LB reset adapter, which MUST preserve original-account behavior unless reset pooling is enabled. Other `/backend-api/` HTTP and WebSocket requests MUST use the fixed `https://chatgpt.com` destination with the original method, path, query, body, Authorization, account identity, cookies and end-to-end headers. The relay MUST remove hop-by-hop headers, preserve streaming and duplicate response headers, close owned upstream resources on disconnect or cancellation, validate upstream TLS, and neither retry state-changing requests nor follow redirects automatically. It MUST reject nonloopback LB destinations, unexpected request authorities and paths outside the backend prefix. It MUST NOT log credentials, query strings or payloads, store caller cookies between requests, or allow arbitrary upstream destinations.

#### Scenario: Usage routing
- **WHEN** Desktop requests either supported usage path
- **THEN** the relay forwards the original caller headers to the strict LB endpoint and returns its quota or error response

#### Scenario: Original identity passthrough
- **WHEN** Desktop requests a settings endpoint
- **THEN** the relay forwards the request to ChatGPT with the original caller identity and unmodified body, and preserves the response status, cookie values and integrity headers

The relay MUST remove only an official-host Domain attribute from non-usage response cookies as described below. It MUST NOT change cookie values or other attributes, or make invalid `__Host-` cookies acceptable by removing their Domain.

#### Scenario: Upstream cookie scoped to the official host
- **WHEN** a non-usage ChatGPT response sets a cookie with Domain equal to `chatgpt.com` or `.chatgpt.com`
- **THEN** the relay removes only that Domain attribute so the browser stores a host-only cookie for the loopback relay
- **AND** it preserves the cookie value, Path, Secure, HttpOnly, SameSite, expiration and every other attribute
- **AND** it leaves host-only cookies and cookies for other domains unchanged and does not retain cookies in a shared server jar

#### Scenario: Configured outbound proxy
- **WHEN** standard outbound proxy environment variables configure ChatGPT HTTP or WebSocket egress
- **THEN** the relay honors the existing HTTP, WebSocket and SOCKS proxy selection policy, including the explicit WebSocket direct-connect override
- **AND** local LB usage requests remain direct loopback traffic

#### Scenario: Unsafe destination or request
- **WHEN** a nonloopback LB URL, unexpected Host, or path outside `/backend-api/` is supplied
- **THEN** the relay rejects it without forwarding credentials

#### Scenario: Failure and cancellation
- **WHEN** an upstream times out, a stream fails, or the downstream disconnects
- **THEN** the relay reports failure without fabricating quota and releases its owned connection and tasks
