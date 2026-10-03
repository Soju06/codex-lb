## ADDED Requirements

### Requirement: Accounts inventory distribution charts

The Accounts page SHALL display Dashboard-style donut charts by plan and account status above Detail, List and Grid views. Each chart SHALL show the total account count and a legend with category counts and percentages of the complete loaded account inventory. Search, filters and pagination SHALL NOT change these statistics. Updated account data SHALL update both charts.

#### Scenario: Plan and status counts

- **WHEN** the inventory has two Plus accounts (active and reauth_required) and one Pro account (deactivated)
- **THEN** each chart shows a total of three accounts
- **AND** the plan chart shows Plus = 2 and Pro = 1 with their percentages
- **AND** the status chart shows Active, Re-auth required and Deactivated as separate groups of one

#### Scenario: All status categories and plan normalization

- **WHEN** the inventory includes active, paused, rate_limited, quota_exceeded, reauth_required and deactivated accounts
- **THEN** each status is counted separately
- **AND** plan keys that differ only in case or surrounding whitespace share a category
- **AND** blank plans and unrecognized statuses appear as Unknown, rather than being omitted or counted as Active
- **AND** unrecognized nonblank plans retain their own category

#### Scenario: Filtering and refreshed data

- **WHEN** the operator searches, filters accounts,
- **THEN** the two charts continue to count all loaded accounts
- **WHEN** the loaded inventory changes after an import, deletion or status update
- **THEN** both charts reflect the latest data

### Requirement: Account distribution presentation states

Account distribution charts SHALL use localized labels, readable textual counts and percentages, and keyboard-focusable legends. They SHALL fit desktop and mobile widths in light and dark themes. Initial loading and failed requests without account data SHALL NOT present a successful empty inventory. A successfully loaded empty inventory SHALL display a zero total, neutral rings and an empty-state label without invented categories or invalid percentages.

#### Scenario: Empty, loading and unavailable inventory

- **WHEN** an account request succeeds with an empty list
- **THEN** both charts show zero and an empty-state message
- **WHEN** no account response has loaded or the initial request fails
- **THEN** the page retains its loading or error feedback without displaying zero-account charts

#### Scenario: Narrow screen and accessible legend

- **WHEN** the Accounts page is viewed at 320px width in any supported language
- **THEN** chart cards and legends fit without horizontal document overflow
- **AND** category counts and percentages are available as text, independently of slice colors
- **AND** focusing a legend entry highlights its corresponding slice
