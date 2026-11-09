## ADDED Requirements

### Requirement: Per-key automatic Daybreak routing
API keys MUST expose a persisted `automatic_daybreak_routing` Boolean through create, list, update and authenticated policy data, and a dashboard create/edit control. Existing and newly created keys MUST default to disabled. Omitting the field on update MUST preserve its value; explicit false MUST disable it. Updating the field MUST invalidate cached key policy. Enabling it MUST NOT expand the key's assigned accounts or grant an upstream entitlement.

#### Scenario: Enable and disable a key
- **WHEN** an operator enables automatic Daybreak routing on one key
- **THEN** authenticated policy reads observe the new value
- **AND** another key's value remains unchanged
- **AND** disabling the first key invalidates its cached value
