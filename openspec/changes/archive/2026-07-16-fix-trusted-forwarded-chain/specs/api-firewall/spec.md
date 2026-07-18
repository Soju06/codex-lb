## MODIFIED Requirements

### Requirement: Trusted proxy header handling

Firewall IP resolution MUST use forwarded client headers only when proxy-header trust is enabled and the socket source IP belongs to the configured trusted proxy CIDR list. `X-Forwarded-For` and RFC 7239 `Forwarded` values MUST be resolved from right to left through one shared trusted-hop algorithm. Other client-IP headers, including `X-Real-IP`, `True-Client-IP`, and `CF-Connecting-IP`, MUST NOT affect firewall identity. Every repeated field value MUST be combined in arrival order before resolution. A missing, malformed, ambiguous, obfuscated, unknown, or non-IP trusted-proxy chain MUST return no resolved client IP so an active firewall allowlist fails closed.

#### Scenario: Trusted proxy chain

- **WHEN** `firewall_trust_proxy_headers=true`
- **AND** the source socket IP and downstream proxy hops match configured trusted CIDRs
- **AND** a valid `X-Forwarded-For` or `Forwarded` chain is present
- **THEN** the firewall resolves the originating client by traversing the chain from right to left

#### Scenario: Trusted proxy appends a separate field

- **WHEN** a client supplies a spoofed loopback value in the first `X-Forwarded-For` or `Forwarded` field
- **AND** a trusted socket proxy appends the actual remote client in a second field
- **THEN** the firewall combines both fields in arrival order
- **AND** resolves the actual remote client rather than the spoofed loopback value

#### Scenario: Singleton proxy headers do not authorize firewall access

- **WHEN** a trusted socket proxy supplies only `X-Real-IP`, `True-Client-IP`, or `CF-Connecting-IP`
- **THEN** firewall client resolution returns no client IP
- **AND** an active firewall allowlist denies the request

#### Scenario: Untrusted proxy source

- **WHEN** the source socket IP is outside the configured trusted CIDR list
- **THEN** the firewall ignores forwarded client headers
- **AND** uses the socket client IP

#### Scenario: Trusted source supplies no complete valid chain

- **WHEN** proxy-header trust is enabled and the source socket IP is trusted
- **AND** the forwarded client chain is missing or contains an invalid hop
- **THEN** firewall client resolution returns no client IP
- **AND** an active firewall allowlist denies the request
