## ADDED Requirements

### Requirement: Contributor coverage survives GitHub account renames

The contributor coverage check MUST resolve the numeric user ID in a GitHub
noreply commit-author address to the current login when that same ID is present
in the repository-contributor or pull-request-author API evidence. It MUST NOT
require a separate contributor entry for that historical login. Unknown IDs
and legacy login-only addresses MUST continue to participate in coverage
checks. Bot filtering, pagination, and failure on incomplete API evidence MUST
remain enforced.

Identity evidence MUST be combined by numeric ID before it is projected to
logins. A PR commit-author response MUST take precedence over a repository
contributor response for the same ID, and live API evidence MUST take
precedence over an older PR event payload.

#### Scenario: Recorded contributor changes their login

- **GIVEN** a human contributor has the same numeric GitHub user ID before and after renaming their account
- **AND** `.all-contributorsrc` contains their current login
- **WHEN** old commits contain the prior login with that numeric ID
- **THEN** the coverage check accepts the current entry without requiring the old login as a second person

#### Scenario: Repository and PR APIs report different names for one account

- **GIVEN** the repository contributor API reports an older login for a numeric user ID
- **AND** the PR commit API reports the current login for that same ID
- **WHEN** the current login is recorded in `.all-contributorsrc`
- **THEN** the coverage check counts one contributor and does not require the superseded login

#### Scenario: Unresolved local authors remain covered

- **GIVEN** a local commit author uses an unknown numeric user ID or a legacy login-only noreply address
- **WHEN** their login is missing from `.all-contributorsrc`
- **THEN** the coverage check fails and reports the missing author

#### Scenario: GitHub identity evidence cannot be completed

- **WHEN** the GitHub contributor or PR commit API fails after its retry budget or cannot provide the complete PR commit list
- **THEN** the coverage check fails rather than accepting partial attribution

### Requirement: Integration-core jobs allow setup and cleanup headroom

The integration-core shard jobs SHALL allow 30 minutes for the complete job,
including setup, test execution, and cleanup. Their individual-test timeouts,
diagnostic watchdog, shard partition, and required all-shards-success aggregate
MUST remain enforced.

#### Scenario: Passing shard exceeds the former whole-job budget

- **GIVEN** all selected tests pass within their individual deadlines
- **AND** setup, test execution, and cleanup together require more than 20 but less than 30 minutes
- **WHEN** the integration-core shard runs
- **THEN** the former 20-minute job limit does not cancel it

#### Scenario: Shard fails or reaches its deadline

- **WHEN** any integration-core shard fails or reaches the 30-minute job deadline
- **THEN** the required integration-core aggregate fails
