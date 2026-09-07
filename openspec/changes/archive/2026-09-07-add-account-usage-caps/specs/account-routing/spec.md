## ADDED Requirements

### Requirement: Independent per-account usage caps

Each account SHALL support independent nullable 5h and weekly consumed-percentage caps. Caps SHALL default to disabled and SHALL accept only finite numbers greater than zero and at most 100, or null to disable. The dashboard API SHALL persist both values atomically, require dashboard write access, and return them in account summaries.

#### Scenario: Save and disable caps
- **WHEN** an operator saves 80 for 5h and 50 for weekly
- **THEN** subsequent account summaries return both values
- **AND** saving null for either cap disables that cap independently

### Requirement: Usage caps constrain new account traffic

The proxy SHALL exclude an account from new request admission when observed usage reaches or exceeds either enabled cap on an applicable standard usage window. This SHALL apply to fresh selection and reused connections, regardless of routing strategy or additional-quota bypass. It SHALL NOT alter stored account status or provider usage. It SHALL preserve account ownership constraints rather than move account-pinned requests to another account. Already admitted work SHALL be allowed to finish.

#### Scenario: Either cap stops admission
- **WHEN** an account capped at 80 percent 5h and 50 percent weekly reports either 80 percent 5h usage or 50 percent weekly usage
- **THEN** no new traffic is admitted to that account after the usage update is observed

#### Scenario: Recovery requires both windows below caps
- **WHEN** the 5h window resets but weekly usage still meets its cap
- **THEN** the account remains excluded
- **AND** once neither applicable window meets its cap the account becomes eligible subject to other routing gates

#### Scenario: Windows are identified by duration
- **WHEN** an account reports only a weekly window in its primary slot
- **THEN** the weekly cap applies and the 5h cap does not
- **AND** missing, elapsed, and monthly windows do not trigger these caps
