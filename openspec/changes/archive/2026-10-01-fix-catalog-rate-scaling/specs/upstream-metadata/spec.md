## ADDED Requirements

### Requirement: Decimal catalog unit conversion

The service MUST preserve validated decimal token-price values while converting
source units for monetary accounting. Conversion MUST NOT round a genuinely
fractional final microdollar cost upward.

#### Scenario: Small LiteLLM input price
- **WHEN** a valid LiteLLM input price is 0.0000002 USD per token
- **AND** a request consumes 100 uncached input tokens and no output tokens
- **THEN** API-key settlement is exactly 20 microdollars

#### Scenario: Fractional final cost
- **WHEN** valid source prices produce a final cost below one microdollar
- **THEN** integer monetary settlement remains zero
