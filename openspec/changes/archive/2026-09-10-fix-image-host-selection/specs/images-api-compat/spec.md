## ADDED Requirements

### Requirement: Internal host selection
Images generation and edit routes MUST select the first candidate with nonempty registry plan visibility and no suppression, ordered as `gpt-5.6-luna`, `gpt-5.5`. If none qualifies, they MUST use `gpt-5.6-luna`. Public image model IDs MUST remain unchanged.

#### Scenario: Cold registry prefers the current host
- **WHEN** the registry uses the bootstrap catalog
- **THEN** the internal model is `gpt-5.6-luna`

#### Scenario: Preferred model unavailable in registry
- **WHEN** only `gpt-5.5` has registry plan visibility without suppression
- **THEN** the internal model is `gpt-5.5`

#### Scenario: No candidate qualifies
- **WHEN** neither candidate has plan visibility without suppression
- **THEN** the selected host is `gpt-5.6-luna` and existing downstream error handling applies
