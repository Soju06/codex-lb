## MODIFIED Requirements

### Requirement: Process-wide network failures are account neutral

The proxy MUST NOT record a transient, permanent, quota, rate-limit, or circuit-breaker health failure against an account when an attempt fails because the local process cannot resolve or route to the upstream host. Routed proxy transport failures MUST retain a credential-safe machine-readable classification after the original exception message is sanitized. A permanent missing proxy hostname MUST remain an endpoint-scoped proxy failure rather than entering process-wide recovery.

A Responses WebSocket receive failure without a complete peer close frame MUST also remain account neutral when a bounded process-local correlation window observes failures from at least two distinct non-empty upstream account ids on the same concrete egress within one second. Concrete egress identity MUST distinguish the actual routed proxy endpoint, parsed environment-proxy endpoint, and direct destination without using exception message text or exposing proxy credentials. Every candidate in the correlated window MUST be classified before account-health settlement as `proxy_network_unavailable`. Downstream keepalive scheduling MUST NOT cancel or restart a pending correlation decision. Once an owned receive task has entered bounded no-close correlation, a request-budget, stream-idle, or eventless-response deadline MUST NOT cancel or settle that receive failure before the correlation decision completes; the completed receive classification MUST reach the existing settlement path first. Repeated failures from one account, failures on different egresses, anonymous accounts, explicit close frames, and live sideband sockets MUST retain their existing classification. Correlation MUST NOT authorize replay of a post-dispatch request, move continuity ownership, or switch accounts.

#### Scenario: Wi-Fi transition does not poison account health

- **WHEN** an upstream attempt fails with a classified local DNS or host-route failure
- **THEN** the selected account's health counters and cooldown state are unchanged
- **AND** the selected account's circuit breaker is unchanged
- **AND** continuity ownership remains pinned to that account

#### Scenario: Routed transient DNS failure remains account neutral after sanitization

- **WHEN** an HTTP or WebSocket attempt through a resolved upstream proxy route fails with transient DNS or local route loss
- **THEN** the credential-safe routed error carries the process-network classification
- **AND** the selected account's health and circuit-breaker state are unchanged

#### Scenario: Missing proxy hostname remains endpoint scoped

- **WHEN** resolving a configured upstream proxy hostname fails with a permanent name-not-found result
- **THEN** the failure remains `upstream_unavailable`
- **AND** the proxy does not classify the host process as disconnected

#### Scenario: Shared egress EOF does not poison account health

- **GIVEN** two Responses WebSockets for distinct upstream accounts use the same concrete egress
- **WHEN** both receive paths fail without complete peer close frames within one second
- **THEN** every correlated failure carries `proxy_network_unavailable`
- **AND** neither account receives a transient health or circuit-breaker failure
- **AND** no interrupted post-dispatch request is replayed or moved to another account

#### Scenario: Downstream keepalive does not restart correlation

- **GIVEN** the configured downstream keepalive interval is shorter than the no-close correlation window
- **WHEN** a Responses receive failure is waiting for its bounded correlation decision
- **THEN** keepalive scheduling does not cancel or restart that receive decision
- **AND** the failure reaches exactly one existing settlement path after correlation completes

#### Scenario: Request deadline does not preempt in-flight correlation

- **GIVEN** a Responses receive task has observed a no-close failure and entered bounded correlation
- **WHEN** the owning request budget, stream-idle window, or eventless-response deadline expires before the correlation decision completes
- **THEN** the receive task completes its bounded classification before settlement
- **AND** a correlated `proxy_network_unavailable` result remains account neutral instead of being replaced by a timeout health outcome

#### Scenario: Single-account and different-egress failures remain account specific

- **WHEN** no-close receive failures repeat only for one upstream account
- **OR** distinct accounts fail through different concrete egresses
- **THEN** the correlation threshold is not satisfied
- **AND** existing `stream_incomplete` account-health behavior remains authoritative

#### Scenario: Explicit close frames are not inferred to be a shared EOF

- **WHEN** an upstream Responses or live sideband WebSocket supplies a close frame
- **THEN** bounded no-close correlation does not reclassify that close
- **AND** the existing close-code and account-health contracts remain authoritative
