## ADDED Requirements

### Requirement: API key overview SHALL show lifetime usage aggregates

The dashboard API key overview SHALL present usage totals using the API key list
`usageSummary` values as lifetime aggregates (all non-warmup request-log history),
unless the backend contract is changed to provide a bounded window explicitly.

#### Scenario: Overview usage labels reflect lifetime scope

- **WHEN** the API key overview renders `usageSummary` values for request count,
  token count, and cost
- **THEN** the section labels SHALL read as lifetime usage (for example:
  "Lifetime Requests", "Lifetime Cost", "Lifetime Cost by API Key", "Lifetime Tokens
  by API Key"), and SHALL NOT be labeled as 7-day totals.
