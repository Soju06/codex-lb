## MODIFIED Requirements

### Requirement: Trusted proxy header handling

For protected HTTP and WebSocket requests, firewall IP resolution MUST use the captured pre-projection raw socket peer as its socket source. It MUST NOT fall back to the downstream projected client when capture is absent. With proxy-header trust disabled, firewall identity MUST be the raw socket peer. With proxy-header trust enabled, the firewall MUST use forwarded client headers only when the raw socket peer belongs to the configured trusted proxy CIDR list. `X-Forwarded-For` and RFC 7239 `Forwarded` values MUST be resolved from right to left through one shared trusted-hop algorithm. Other client-IP headers, including `X-Real-IP`, `True-Client-IP`, and `CF-Connecting-IP`, MUST NOT affect firewall identity. Every repeated field value MUST be combined in arrival order before resolution. A missing raw-peer capture or a missing, malformed, ambiguous, obfuscated, unknown, or non-IP trusted-proxy chain MUST return no resolved client IP so an active firewall allowlist fails closed. An empty allowlist MUST remain allow-all. Downstream consumers MUST continue to observe the projected client and scheme.

#### Scenario: Proxy trust disabled ignores a listed projection

- **WHEN** proxy-header trust is disabled
- **AND** the captured raw peer is not allowlisted
- **AND** proxy projection replaces the downstream client with an allowlisted address
- **THEN** both protected HTTP and WebSocket firewall enforcement deny the request
- **AND** downstream projection does not determine firewall identity

#### Scenario: Proxy trust disabled allows a listed raw peer

- **WHEN** proxy-header trust is disabled
- **AND** the captured raw peer is allowlisted
- **AND** proxy projection replaces the downstream client with an unlisted address
- **THEN** both protected HTTP and WebSocket firewall enforcement allow the request
- **AND** downstream consumers continue to observe the projected client and scheme

#### Scenario: Untrusted raw peer cannot establish forwarded trust

- **WHEN** proxy-header trust is enabled
- **AND** the captured raw peer is outside configured trusted CIDRs and is not allowlisted
- **AND** proxy projection replaces the downstream client with a trusted, allowlisted address
- **THEN** both protected HTTP and WebSocket firewall enforcement deny the request

#### Scenario: Missing capture fails closed with active allowlist

- **WHEN** raw-peer capture is absent
- **AND** the allowlist contains one or more entries
- **THEN** both protected HTTP and WebSocket firewall enforcement deny the request
- **AND** the projected client is not used as fallback identity

#### Scenario: Missing capture preserves empty allow-all mode

- **WHEN** raw-peer capture is absent
- **AND** the allowlist is empty
- **THEN** both protected HTTP and WebSocket firewall enforcement allow the request

#### Scenario: Trusted proxy chain

- **WHEN** `firewall_trust_proxy_headers=true`
- **AND** the captured raw socket peer and downstream proxy hops match configured trusted CIDRs
- **AND** a valid `X-Forwarded-For` or `Forwarded` chain is present
- **THEN** the firewall resolves the originating client by traversing the chain from right to left

#### Scenario: Trusted proxy appends a separate field

- **WHEN** a client supplies a spoofed loopback value in the first `X-Forwarded-For` or `Forwarded` field
- **AND** a trusted raw socket proxy appends the actual remote client in a second field
- **THEN** the firewall combines both fields in arrival order
- **AND** resolves the actual remote client rather than the spoofed loopback value

#### Scenario: Singleton proxy headers do not authorize firewall access

- **WHEN** a trusted raw socket proxy supplies only `X-Real-IP`, `True-Client-IP`, or `CF-Connecting-IP`
- **THEN** firewall client resolution returns no client IP
- **AND** an active firewall allowlist denies the request

#### Scenario: Untrusted proxy source

- **WHEN** the captured raw socket peer is outside the configured trusted CIDR list
- **THEN** the firewall ignores forwarded client headers
- **AND** uses the raw socket peer IP

#### Scenario: Trusted source supplies no complete valid chain

- **WHEN** proxy-header trust is enabled and the captured raw socket peer is trusted
- **AND** the forwarded client chain is missing or contains an invalid hop
- **THEN** firewall client resolution returns no client IP
- **AND** an active firewall allowlist denies the request
