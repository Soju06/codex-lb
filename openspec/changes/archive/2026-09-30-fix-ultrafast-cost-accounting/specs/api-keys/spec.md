## ADDED Requirements

### Requirement: Ultrafast costs follow the billable response tier

Request-log costs and API-key cost-limit settlement MUST use the effective
upstream response tier and the same Ultrafast token prices for streaming and
non-streaming Responses requests. Cached reads MUST remain distinct from
ordinary input. Settlement MUST remain idempotent.
Cost limits MUST retain fractional-microdollar truncation without losing an
integral microdollar to binary floating-point representation error.

#### Scenario: Confirmed Ultrafast response
- **WHEN** a request asks for Ultrafast and upstream reports Ultrafast
- **THEN** the log preserves requested and actual Ultrafast metadata
- **AND** persisted costs and settled limits use Ultrafast rates

#### Scenario: Ultrafast request downgraded to default
- **WHEN** a request asks for Ultrafast but upstream reports default
- **THEN** the log retains requested and actual tiers separately
- **AND** costs and settled limits use standard rates

#### Scenario: Previously recorded cost
- **WHEN** the pricing fix is installed
- **THEN** existing non-NULL costs are not rewritten merely because a request asked for Ultrafast

#### Scenario: Small Ultrafast requests settle exact whole microdollars
- **WHEN** an Ultrafast response has 32 input tokens and 7 output tokens
- **THEN** settlement records 4020 microdollars
- **AND** 40 input tokens including 3 cached tokens and 3 output tokens settle 3138 microdollars
- **AND** genuinely fractional microdollar costs are truncated, not rounded up
