## ADDED Requirements

### Requirement: API Keys Declare Traffic Class

API keys SHALL have a `traffic_class` value. The default SHALL be `foreground`. The system SHALL also accept `opportunistic` for clients that may only use burnable quota.

#### Scenario: Create opportunistic key
- **WHEN** admin creates an API key with `trafficClass: "opportunistic"`
- **THEN** the key is persisted and returned with `trafficClass: "opportunistic"`

#### Scenario: Omitted traffic class defaults to foreground
- **WHEN** admin creates an API key without `trafficClass`
- **THEN** the key is persisted and returned with `trafficClass: "foreground"`
