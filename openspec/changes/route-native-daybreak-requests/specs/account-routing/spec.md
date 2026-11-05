## ADDED Requirements

### Requirement: Automatic Daybreak scope is an intersection
Automatic Daybreak routing MUST pass the existing security-work-authorized requirement to account selection before the first attempt and every retry. Eligible accounts MUST remain within the API key's permitted scope and satisfy the existing account-level Trusted Cyber toggle, ownership, availability and model constraints. An empty eligible pool MUST fail without ordinary-account fallback.

#### Scenario: Authorized account is unavailable or outside the key scope
- **WHEN** no permitted account satisfies Trusted Cyber eligibility and existing routing constraints
- **THEN** the request fails before ordinary-account dispatch

### Requirement: DBB affinity does not rewrite ordinary process locality

Required routing MUST use conversation affinity without reading or seeding the
shared ordinary process preference. This MUST preserve existing ordinary
account-selection rules, API-key scope, hard ownership, retry eligibility and
reservation settlement. DBB routing MUST NOT enable affinity when all applicable
affinity controls are disabled.

#### Scenario: DBB-first traffic preserves ordinary burn_first
- **WHEN** a DBB conversation is selected before ordinary traffic in a shared process
- **THEN** a separate ordinary conversation still selects its burn_first account

#### Scenario: Mid-conversation DBB switch respects ownership
- **WHEN** an ordinary conversation requests DBB on a DBB-incapable account
- **THEN** an account-neutral request can select a capable account
- **AND** existing account-owned response IDs, files or opaque history cannot be transferred to an incompatible subscription

#### Scenario: Disabled affinity stays disabled
- **WHEN** session, cache, and thread affinity are disabled
- **THEN** a DBB request with a prompt cache key does not create a sticky selection policy
