## ADDED Requirements

### Requirement: Response-create dump directory is bounded without configuration

The oversized response-create dump directory under `<data-dir>/debug/response-create-dumps` MUST be bounded on the base install path with no operator configuration. When the service captures an oversized `response.create` payload, it MUST NOT write a new dump if a dump for the same payload fingerprint is already stored, and after storing a dump it MUST remove the oldest stored dumps so that at most a fixed number of dump pairs remain. Each dump is a pair of a gzipped payload file and a meta file that MUST be added and removed together. Suppressing a duplicate MUST remain operator-visible in the logs, because the recurrence signal is the reason the dump path exists.

#### Scenario: Repeated identical payloads are stored once

- **GIVEN** an oversized `response.create` payload has already been dumped
- **WHEN** a retry of the byte-identical payload is dumped again
- **THEN** no additional dump pair is written
- **AND** the originally stored dump pair is retained
- **AND** the suppressed duplicate is logged with its payload fingerprint and the path of the existing dump

#### Scenario: Distinct payloads are stored separately

- **GIVEN** an oversized `response.create` payload has already been dumped
- **WHEN** a different oversized payload is dumped
- **THEN** a separate dump pair is written for it

#### Scenario: Oldest dumps are pruned once the directory is full

- **GIVEN** the dump directory already holds the maximum number of dump pairs
- **WHEN** a dump for a new payload is written
- **THEN** the oldest dump pairs are removed so the maximum is not exceeded
- **AND** each removed payload file has its meta file removed with it
- **AND** the newly written dump pair is retained

#### Scenario: Dump retention needs no setting

- **GIVEN** a default installation with no dump-related configuration
- **WHEN** oversized response-create dumps are captured over time
- **THEN** duplicate suppression and pruning apply
- **AND** no `CODEX_LB_*` setting is required to bound the directory
