## ADDED Requirements

### Requirement: Fleet summary reports normalized utilization safely

The fleet summary SHALL add generation time, included and excluded account
identifiers, normalized five-hour and weekly used percentages, freshness state
and reason, and additional quota information. Percentages MUST represent used
capacity, where zero means unused and one hundred means exhausted, MUST be
clamped to 0–100, and MUST be rounded to the nearest whole display percentage.

#### Scenario: Fresh active accounts produce a headline

- **WHEN** every included active account has fresh five-hour usage data
- **THEN** the response includes a normalized five-hour used percentage
- **AND** fresh exhausted capacity contributes 100 percent
- **AND** paused or deactivated accounts are listed as excluded

#### Scenario: Included account data is stale or missing

- **WHEN** any included active account has stale or missing five-hour data
- **THEN** the response marks the headline stale with a reason
- **AND** it does not present a healthy normalized headline

### Requirement: Fleet summary additions preserve API-key policy

The added fields MUST remain behind existing fleet Bearer API-key
authentication, account scoping, and quota-visibility policy.

#### Scenario: Key lacks quota visibility

- **WHEN** the authenticated key cannot view account-pool usage
- **THEN** normalized utilization and quota details are not exposed
