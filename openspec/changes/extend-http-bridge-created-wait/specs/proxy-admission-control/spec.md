# Delta: proxy-admission-control

## ADDED Requirements

### Requirement: HTTP bridge missing-created acknowledgement cap is 100 seconds
The owner-side missing-response-created watchdog SHALL use the smaller of the stuck-gate retirement threshold and 100 seconds. It MUST preserve shorter enclosing budgets, existing eligibility and progress rules, and suppression of automatic replay for ambiguously submitted image requests.

#### Scenario: Eventless request may wait beyond the former cap
- **GIVEN** an eligible HTTP bridge owner sent response.create at monotonic time 100 with a stuck threshold of 300 seconds
- **WHEN** no matching response-created or downstream-visible evidence arrives
- **THEN** the watchdog deadline is monotonic time 200, not 160

#### Scenario: Shorter stuck threshold still wins
- **GIVEN** the same owner and a stuck threshold of 30 seconds
- **WHEN** its deadline is computed
- **THEN** the deadline is monotonic time 130
