## ADDED Requirements

### Requirement: Assigned-account quota badges reflect monthly-only free accounts

The API key create and edit dialogs SHALL display assigned-account quota badges according to the normalized quota model of each account.

#### Scenario: Free account shows monthly badge only
- **WHEN** assigned-account selection renders a free account whose normalized quota model is monthly-only
- **THEN** the dialog shows a `Monthly <percent>% left` badge for that account
- **AND** it does not show a weekly-left badge for that account

#### Scenario: Paid account retains 5h and 7d badges
- **WHEN** assigned-account selection renders an account with normalized 5h and 7d quota windows
- **THEN** the dialog shows `5h <percent>% left` and `7d <percent>% left` badges for that account
