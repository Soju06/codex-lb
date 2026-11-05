## ADDED Requirements

### Requirement: Automatic Daybreak scope is an intersection
Automatic Daybreak routing MUST pass the existing security-work-authorized requirement to account selection before the first attempt and every retry. Eligible accounts MUST remain within the API key's permitted scope and satisfy the existing account-level Trusted Cyber toggle, ownership, availability and model constraints. An empty eligible pool MUST fail without ordinary-account fallback.

#### Scenario: Authorized account is unavailable or outside the key scope
- **WHEN** no permitted account satisfies Trusted Cyber eligibility and existing routing constraints
- **THEN** the request fails before ordinary-account dispatch

### Requirement: DBB affinity does not rewrite ordinary process locality
Required routing MUST use conversation affinity without reading or seeding the ordinary process preference. It MUST preserve ordinary selection, key scope, ownership, retry and settlement rules, and MUST NOT enable disabled affinity controls.

#### Scenario: DBB-first traffic preserves ordinary burn_first
- **WHEN** DBB work precedes a separate ordinary conversation in a shared process
- **THEN** the ordinary conversation still selects its burn_first account

#### Scenario: Mid-conversation switch preserves ownership
- **WHEN** an ordinary conversation requests DBB on an incapable account
- **THEN** account-neutral work can select a capable account
- **AND** incompatible response IDs, files or opaque history cannot transfer accounts

#### Scenario: Disabled affinity stays disabled
- **WHEN** session, cache and thread affinity are disabled
- **THEN** a DBB prompt cache key does not create stickiness
