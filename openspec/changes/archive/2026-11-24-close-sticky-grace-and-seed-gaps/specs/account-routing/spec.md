## ADDED Requirements

### Requirement: Sticky grace and seed initialization respect admission

A capped soft owner MUST NOT win a rate-limit grace draw when the request permits cap spillover. A first thread that can use an eligible healthy account MUST defer discretionary recovery-probe admission until its process seed is initialized. The thread binding and seed MUST use the existing atomic write.

#### Scenario: Capped soft owner is inside its grace window

- **GIVEN** the soft owner is at its lease cap and its rate limit resets within the grace window
- **AND** the request permits cap spillover and an eligible sibling has capacity
- **WHEN** the proxy selects an account
- **THEN** the eligible sibling can serve the request and the soft affinity is retained

#### Scenario: The first thread encounters a due recovery probe

- **GIVEN** the process seed is absent and a healthy eligible account is available
- **WHEN** a first thread would require a recovery-probe reservation
- **THEN** selection defers the probe and persists the admitted thread and process seed together
