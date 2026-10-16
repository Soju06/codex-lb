# api-keys Delta

## ADDED Requirements

### Requirement: API keys may cap their estimated share of subscription quota

An API key MAY store `usage_share_percent` (`usageSharePercent` in dashboard API payloads) as an integer from 1 through 100 inclusive. `null` or omission SHALL disable this policy. Create, update, list, detail, regenerate, and authentication-cache projection SHALL preserve the field. Existing keys SHALL remain unrestricted after migration.

The percentages of different keys SHALL be independent caps and SHALL NOT be required to sum to 100, because keys may have different account scopes and operators may intentionally overcommit capacity.

#### Scenario: Create a key with an estimated usage share

- **WHEN** an operator creates an API key with `usageSharePercent: 20`
- **THEN** the response reports `usageSharePercent: 20`
- **AND** later authenticated requests carry the policy in their `ApiKeyData`

#### Scenario: Clear an estimated usage share

- **GIVEN** a key has `usageSharePercent: 20`
- **WHEN** an operator updates it with `usageSharePercent: null`
- **THEN** subsequent responses report `null`
- **AND** share admission is disabled for that key

#### Scenario: Reject an invalid estimated usage share

- **WHEN** create or update supplies `0`, `101`, or a non-integer value
- **THEN** the API rejects the request as invalid

#### Scenario: Existing and unrelated edits preserve policy

- **GIVEN** a migrated key has no usage-share policy, or a configured key has one
- **WHEN** the migration runs or an unrelated field is edited without supplying `usageSharePercent`
- **THEN** the existing null or configured value is preserved
