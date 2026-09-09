# usage-refresh-policy Delta

## ADDED Requirements

### Requirement: Refresh singleflight settlement cannot poison a successor

When a refresh task completes, it MUST mutate the inflight entry and
refresh-failure cache only if it is still the current inflight task for that
singleflight key. A completion from an older attempt MUST NOT publish or clear
negative-cache state belonging to a successor refresh.

#### Scenario: Failed attempt is followed by a live successor

- **GIVEN** a refresh task fails for a key
- **AND** a successor task for the same key is installed before the failed
  task's completion settlement runs
- **WHEN** another caller arrives while the successor is still in flight
- **THEN** the caller joins the successor task
- **AND** the failed attempt's error is not served from the negative cache

#### Scenario: Existing failure settlement has no successor

- **GIVEN** a refresh task fails and remains the current inflight task
- **WHEN** its completion settlement runs
- **THEN** the configured negative-cache cooldown behavior is preserved
