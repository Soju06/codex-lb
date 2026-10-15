## ADDED Requirements

### Requirement: Default account probe model selection
Account probes without an explicit model MUST use the same ordered registry selection as Images: `gpt-5.6-luna`, then `gpt-5.5`, requiring nonempty plan visibility and no suppression, with `gpt-5.6-luna` as fallback. Explicit probe models MUST remain unchanged.

#### Scenario: Cold registry prefers the current host
- **WHEN** the registry uses the bootstrap catalog
- **THEN** the internal model is `gpt-5.6-luna`

#### Scenario: Preferred model unavailable in registry
- **WHEN** only `gpt-5.5` has registry plan visibility without suppression
- **THEN** the internal model is `gpt-5.5`

#### Scenario: No candidate qualifies
- **WHEN** neither candidate has plan visibility without suppression
- **THEN** the selected host is `gpt-5.6-luna` and existing downstream error handling applies
