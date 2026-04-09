## ADDED Requirements

### Requirement: Platform fallback uses the remaining percentages visible to operators

For phase-1 public-route fallback, the service MUST treat a compatible ChatGPT-web candidate as healthy only while it has both `primary_remaining_percent > 10` and `secondary_remaining_percent > 5`. When no compatible ChatGPT-web candidate remains healthy under those thresholds, the service MAY consider `openai_platform` as fallback, subject to the existing route-family eligibility checks.

#### Scenario: A compatible ChatGPT-web candidate with more than 10 percent primary remaining and more than 5 percent secondary remaining keeps Platform idle

- **WHEN** a request targets an eligible public HTTP route
- **AND** both `chatgpt_web` and `openai_platform` are configured for that route family
- **AND** at least one compatible ChatGPT-web candidate has both `primary_remaining_percent > 10` and `secondary_remaining_percent > 5`
- **THEN** the service keeps routing through the ChatGPT-web pool

#### Scenario: Platform fallback may activate once no compatible candidate remains healthy under the remaining-percent thresholds

- **WHEN** a request targets an eligible public HTTP route
- **AND** both `chatgpt_web` and `openai_platform` are configured for that route family
- **AND** each compatible ChatGPT-web candidate has `primary_remaining_percent <= 10` or `secondary_remaining_percent <= 5`
- **THEN** the service MAY select the Platform identity as fallback for that request
