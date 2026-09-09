## ADDED Requirements

### Requirement: Reset consumption has durable recovery
The service MUST persist one active logical reset attempt with its account, credit, trigger and stable redemption request ID before calling upstream. An uncertain result MUST retain that attempt and MUST NOT cause consumption of another credit. The service MUST reconcile inventory and usage before retrying an uncertain attempt, MUST reuse its request ID, and MUST persist confirmed consumption before usage refresh. An applied attempt MUST block new consumption until fresh usage proves recovery. Successful exhaustion cooldowns MUST survive restart.

#### Scenario: Upstream timeout after consumption
- **WHEN** upstream consumption has an unknown result
- **THEN** another account or credit MUST NOT be consumed
- **AND** a later recovery attempt MUST use the original account, credit and request ID.

#### Scenario: Refresh fails after confirmed consumption
- **WHEN** upstream confirms reset and usage refresh fails
- **THEN** the durable attempt MUST remain applied
- **AND** recovery MUST refresh or probe without consuming another credit.

#### Scenario: Concurrent or restarted workers
- **WHEN** redemption requests overlap or the service restarts
- **THEN** only one active logical redemption MAY exist
- **AND** the existing attempt and persisted cooldown MUST govern subsequent work.

### Requirement: Exhaustion spending uses fresh standard usage
Before an exhaustion-triggered redemption, the scheduler MUST refresh each subscription-usable serving OpenAI account and re-evaluate standard quota availability. Unknown or failed refresh MUST suppress new consumption. Healthy capacity MUST suppress exhaustion redemption. Additional model quota exhaustion alone MUST NOT trigger a standard reset. Paused, disconnected and subscription-unusable accounts MUST remain excluded. Zero available credits MUST cause no consumption.

#### Scenario: Persisted exhaustion is stale
- **WHEN** fresh usage shows an available standard window on any serving account
- **THEN** no exhaustion credit is consumed and ordinary routing can use recovered capacity.

#### Scenario: Full exhaustion and a saved credit
- **WHEN** fresh standard usage confirms every serving OpenAI account is exhausted and a saved credit is available
- **THEN** the scheduler MAY redeem one credit through the durable recovery flow.

### Requirement: Expiry policy and manual contract remain compatible
Expiring-credit redemption MUST retain its separate expiry eligibility policy and manual API responses MUST retain their existing schema. Both paths MUST respect the same active redemption attempt so neither creates duplicate or competing consumption while an outcome remains uncertain.

#### Scenario: Expiry overlaps an uncertain exhaustion reset
- **WHEN** an exhaustion reset remains pending
- **THEN** the expiry sweep MUST reconcile it before creating another redemption.
