## ADDED Requirements

### Requirement: Compact request-path latency is bounded without changing default CLI timeout parity
When `/responses/compact` performs account selection, token refresh, or upstream connection setup, the proxy MUST enforce a configurable request-path budget for those pre-response phases. The proxy MUST preserve the existing default compact behavior of not imposing an upstream read timeout unless an operator explicitly configures one.

#### Scenario: Compact request budget expires before upstream response handling begins
- **WHEN** a compact request exhausts its configured request-path budget during account selection, token refresh, or upstream connection setup
- **THEN** the proxy returns `502` with OpenAI-format error code `upstream_unavailable`
- **AND** it does not begin another retry attempt

#### Scenario: Default compact read path remains unbounded
- **WHEN** `/responses/compact` is called without an explicit compact read-timeout override
- **THEN** the proxy may still bound selection, refresh, and connect work
- **AND** it MUST NOT add a default upstream read timeout beyond the existing compact contract
