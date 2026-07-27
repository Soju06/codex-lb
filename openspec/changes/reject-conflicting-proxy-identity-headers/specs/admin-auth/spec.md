## ADDED Requirements

### Requirement: Trusted-proxy locality requires raw-peer identity consensus

When proxy-header trust is enabled, locality decisions used by dashboard bootstrap and disabled API-key authentication MUST evaluate trusted-proxy source membership against the launcher-preserved raw socket peer. If that peer is unavailable, the request MUST NOT establish proxy-derived locality. For a trusted peer, every allowed identity family among `X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `True-Client-IP`, and `CF-Connecting-IP` that contains a non-whitespace value MUST be resolved independently with its established validation and trusted-hop behavior. The request MUST use a header-derived identity only when every populated family resolves successfully to the same IP. A malformed or unresolvable populated family, or differing resolved IPs, MUST fail closed and MUST NOT classify the request as local. Empty-only families MUST be ignored; same-family chain and singleton-duplicate behavior MUST remain unchanged. Untrusted raw peers and generic resolver callers MUST retain their established behavior.

#### Scenario: Redundant proxy families agree

- **WHEN** a trusted raw peer supplies multiple populated identity families that independently resolve to the same IP
- **THEN** locality uses that IP
- **AND** common `X-Forwarded-For` plus `CF-Connecting-IP` or `X-Real-IP` combinations remain valid

#### Scenario: Proxy families disagree or are invalid

- **WHEN** a trusted raw peer supplies populated families that resolve to different IPs, or any populated family cannot be resolved
- **THEN** the request is not classified as local

#### Scenario: Empty and repeated fields retain family behavior

- **WHEN** a family is empty-only, a chain family is repeated, or a singleton family is duplicated
- **THEN** empty-only evidence is ignored
- **AND** established chain combination and singleton duplicate rejection still apply

#### Scenario: Untrusted peer and generic resolver behavior are unchanged

- **WHEN** the raw peer is untrusted or a caller uses the generic client-IP resolver
- **THEN** the existing socket-identity or header-precedence behavior applies without locality consensus
