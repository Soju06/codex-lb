## ADDED Requirements

### Requirement: Trusted proxy client identity resists appended Forwarded chain spoofing

When proxy-header trust is enabled and the socket peer belongs to a configured trusted proxy CIDR, the system MUST resolve an RFC 7239 `Forwarded` client chain from right to left. It MUST advance toward an earlier `for=` hop only while the immediately downstream peer is trusted. Every forwarded element MUST contain exactly one valid IP `for=` node, optionally with a valid port. Every parameter name and value MUST follow RFC 7239 token or quoted-string syntax, and no parameter name may repeat within an element. IPv6 nodes MUST be bracketed and quoted, and every node carrying a port MUST be quoted; numeric ports MUST contain one to five ASCII digits and fall within `0..65535`. Otherwise the entire `Forwarded` value MUST fail closed and MUST NOT classify the request as local.

`X-Real-IP`, `True-Client-IP`, and `CF-Connecting-IP` MUST each occur at most once. Repetition of any such singleton client-IP header MUST return no resolved client IP and MUST NOT classify the request as local.

#### Scenario: Client-preseeded loopback value cannot bypass remote bootstrap protection

- **WHEN** a trusted socket proxy appends `for=203.0.113.24` to a client-supplied `Forwarded: for=127.0.0.1` value
- **THEN** the resolved client is `203.0.113.24`
- **AND** the request is not classified as local

#### Scenario: Proxy appends a separate Forwarded field

- **WHEN** a client supplies `Forwarded: for=127.0.0.1`
- **AND** a trusted socket proxy appends a second `Forwarded: for=203.0.113.24` field
- **THEN** the system combines both field values in arrival order
- **AND** resolves the client as `203.0.113.24`
- **AND** does not classify the request as local

#### Scenario: Complete trusted multi-proxy chain resolves the originating client

- **WHEN** the socket peer and each downstream proxy hop belong to configured trusted proxy CIDRs
- **AND** the `Forwarded` elements contain one valid IP `for=` node per hop
- **THEN** the system resolves the originating client IP from the earliest reachable element

#### Scenario: Malformed or incomplete Forwarded chain fails closed

- **WHEN** any `Forwarded` element has a missing, duplicate, obfuscated, unknown, or malformed `for=` node
- **THEN** trusted proxy client resolution returns no client IP from that header
- **AND** the request is not classified as local

#### Scenario: Unquoted IPv6 or port-bearing node fails closed

- **WHEN** a `Forwarded` element contains an unquoted bracketed IPv6 node or an unquoted node with a port
- **THEN** trusted proxy client resolution returns no client IP from that header
- **AND** the request is not classified as local

#### Scenario: Bracketed IPv6 node with port is resolved

- **WHEN** a trusted socket proxy supplies a valid quoted bracketed IPv6 `for=` node with a numeric port
- **THEN** the system resolves the IPv6 address without the brackets or port

#### Scenario: Repeated singleton client-IP header fails closed

- **WHEN** a trusted socket request contains more than one field for `X-Real-IP`, `True-Client-IP`, or `CF-Connecting-IP`
- **THEN** trusted proxy client resolution returns no client IP
- **AND** the request is not classified as local
