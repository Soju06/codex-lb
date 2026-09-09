## MODIFIED Requirements

### Requirement: Scheduler is safe in multi-replica deployments

The system MUST guarantee at-most-once execution for each due schedule slot `(job_id, scheduled_for)` across replicas. A running claim MUST be reclaimable only after it has been held longer than its own reclaim window, and that window MUST be derived from the compact request budget captured on the run row when the claim was made (`automation_runs.claim_budget_seconds`, stored on every fresh claim and every reclaim), not from the budget in effect when staleness is judged. A run's compact request MUST execute with that same captured budget. Rows claimed before the budget was stored (NULL) MUST fall back to the current effective compact request budget.

#### Scenario: Two replicas contend for the same due job

- **WHEN** two scheduler instances observe the same due job
- **THEN** only one instance successfully claims and executes that `(job_id, scheduled_for)` slot
- **AND** the other instance skips execution without creating duplicate run records

#### Scenario: Scheduler run claiming remains deterministic after retries

- **WHEN** one scheduler replica crashes after claiming a slot and before completion
- **THEN** no second replica creates a duplicate claim row for the same slot
- **AND** the existing run row remains the single source of truth for that slot

#### Scenario: Lowering the dashboard compact budget does not reclaim an in-flight run

- **GIVEN** a run claimed while the effective compact request budget was 600 seconds, 200 seconds ago
- **WHEN** the dashboard lowers the compact request budget to 60 seconds and the scheduler evaluates the slot
- **THEN** the run is not reclaimed and no second compact ping is started for that slot
- **AND** the run becomes reclaimable only once it has been held longer than 630 seconds

#### Scenario: Legacy claim without a captured budget follows the current budget

- **GIVEN** a running claim whose `claim_budget_seconds` is NULL, held for 200 seconds
- **WHEN** the effective compact request budget is 600 seconds
- **THEN** the claim is still in flight
- **AND** once the effective budget is 60 seconds the same claim is reclaimed
