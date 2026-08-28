## ADDED Requirements

### Requirement: Ultrafast routing follows live account entitlement

The system MUST treat `ultrafast` as an access-controlled service tier and MUST derive account eligibility from live or retained per-account upstream catalog metadata. The bundled bootstrap catalog MUST NOT invent Ultrafast entitlement.

#### Scenario: Only an advertising account is eligible

- **GIVEN** two accounts advertise `gpt-5.6-sol`
- **AND** only one account advertises the `ultrafast` service tier
- **WHEN** a request explicitly asks for `service_tier: "ultrafast"`
- **THEN** account selection considers only the advertising account

#### Scenario: Bootstrap metadata does not grant preview access

- **WHEN** no live or retained account catalog advertises `ultrafast`
- **THEN** bootstrap model metadata does not expose or grant that tier
