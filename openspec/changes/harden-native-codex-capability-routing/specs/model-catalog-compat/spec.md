## ADDED Requirements

### Requirement: Refreshed account catalogs constrain pooled routing

When per-account upstream model catalogs have been refreshed, the system MUST
treat those account-level results as authoritative for pooled request routing.
The merged catalog MAY expose the union of models and service tiers for client
discovery, but a request MUST be routed only to an active account whose own
catalog advertised the requested model and non-default service tier.

#### Scenario: One account lacks a unioned model

- **GIVEN** one active account advertises `gpt-5.6-sol` and another active
  account does not
- **WHEN** a request selects `gpt-5.6-sol`
- **THEN** only the advertising account is eligible

#### Scenario: No account advertises requested Fast tier

- **GIVEN** refreshed account catalogs are authoritative
- **AND** no active account advertises `priority` for the requested model
- **WHEN** a request selects `service_tier = "priority"`
- **THEN** selection fails closed instead of falling back to plan-only routing

#### Scenario: Active pool becomes empty

- **WHEN** all accounts are paused, deactivated, or otherwise excluded from
  model refresh
- **THEN** the refreshed registry publishes an authoritative empty snapshot
- **AND** stale models from inactive accounts are not served or routed

