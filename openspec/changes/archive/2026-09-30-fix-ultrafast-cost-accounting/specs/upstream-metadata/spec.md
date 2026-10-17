## ADDED Requirements

### Requirement: Ultrafast API-equivalent token pricing

The service MUST recognize explicit Ultrafast input, cached-input and output
prices, including long-context groups, in validated catalogs and persisted
snapshots. It MUST retain known Ultrafast fields during compatible partial
refreshes and provide current Astra prices without network access.
Malformed or incomplete tier groups MUST NOT replace valid model pricing.

#### Scenario: Astra short-context Ultrafast estimate
- **WHEN** Astra bills Ultrafast with 100000 uncached input and 10000 output tokens
- **THEN** the calculated API-equivalent cost is 9.00 USD
- **AND** subscription quota percentages and plan capacities are unchanged

#### Scenario: Astra long-context boundary
- **WHEN** Astra bills Ultrafast with more than 272000 input tokens
- **THEN** input, cached-input and output rates are 120, 12 and 450 USD per million
- **AND** at exactly 272000 input tokens the rates remain 60, 6 and 300

#### Scenario: Offline startup and compatible refresh
- **WHEN** startup has only the bundled snapshot or an older compatible persistent snapshot
- **THEN** Astra Ultrafast prices are available
- **AND** a compatible standard-only update retains them

#### Scenario: Invalid Ultrafast metadata
- **WHEN** a catalog contains incomplete, negative or non-finite Ultrafast prices
- **THEN** those prices do not replace previously valid model pricing
