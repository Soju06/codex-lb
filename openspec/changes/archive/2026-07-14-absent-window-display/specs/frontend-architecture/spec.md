## ADDED Requirements

### Requirement: Account quota displays hide expired windows

Account summary payloads SHALL present the primary (short) quota window as absent — null remaining percentage, remaining credits, reset timestamp, and window duration — when its last usage sample has an elapsed `reset_at`, instead of freezing the stale sample. Accounts without any primary sample SHALL NOT display an optimistic 100% primary remaining default. Long (weekly/monthly) window displays keep the raw samples: their consumers advance elapsed resets by design (weekly credit pace) and upstream still reports them, so staleness is transient. Displayed account status SHALL apply the same expired-window treatment routing applies before deriving the badge: an elapsed primary sample is a reset window, not exhaustion evidence, so a frozen ≥100% sample MUST NOT surface a rate-limited badge that the selector would never apply.

#### Scenario: Expired 5h sample displays as absent

- **GIVEN** upstream stopped reporting the short window and an account's last primary sample has an elapsed `reset_at`
- **WHEN** the dashboard loads account summaries
- **THEN** the account's primary window fields are null
- **AND** the UI renders the 5h quota as absent, matching the weekly-only presentation

#### Scenario: Expired exhausted sample does not display rate-limited

- **GIVEN** an active account whose last primary sample reports 100% used with an elapsed `reset_at`
- **WHEN** account summaries are built
- **THEN** the primary window fields are null
- **AND** the account status stays active instead of inferring rate-limited from the stale sample

#### Scenario: Missing primary data is not optimistic

- **WHEN** an account has no primary usage sample and is not a weekly-only plan
- **THEN** `primary_remaining_percent` is null rather than 100

#### Scenario: Live windows are unaffected

- **WHEN** an account's primary sample has an unexpired `reset_at`
- **THEN** the summary displays its used/remaining percentages, reset, and duration unchanged
