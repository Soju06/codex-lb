## ADDED Requirements

### Requirement: Source compact retains enforced tier attribution

Explicit compact requests selected for a Responses model source MUST retain their enforced service tier for source admission and requested-tier attribution. Subscription model tier fallback MUST run only when the request follows subscription-account routing.

#### Scenario: Assigned source shares a native model without the enforced tier
- **GIVEN** an API key assigns a Responses source and enforces priority
- **AND** the source model also exists in an authoritative subscription catalog that advertises only default
- **WHEN** either explicit compact endpoint selects that source
- **THEN** the request log records requested service tier priority
- **AND** the subscription fallback does not clear that tier
