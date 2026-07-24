## ADDED Requirements

### Requirement: Heartbeat metadata publishes per-account stream-lease counts

Each replica's ring heartbeat metadata MUST include the replica's current per-account in-flight stream-lease counts (omitting zero-count accounts), refreshed on every heartbeat upsert. Publishing the counts MUST NOT require an advertised bridge endpoint: a replica without a configured advertise URL MUST still publish its counts, and endpoint-requiring membership reads MUST determine endpoint-bearing members from the advertised endpoint field in the metadata rather than from metadata presence. Ring readers consuming the counts MUST treat a member's counts as fresh only when that member's heartbeat is within the ring stale threshold, and MUST treat metadata without published counts as missing rather than zero so mixed-version rings never fabricate idle capacity. Publishing the counts MUST NOT add reads or writes beyond the existing heartbeat upsert.

#### Scenario: Heartbeat carries in-flight counts

- **GIVEN** a replica holding 3 in-flight stream leases for account A
- **WHEN** its ring heartbeat fires
- **THEN** the upserted metadata includes account A with count 3
- **AND** accounts with zero in-flight leases are omitted

#### Scenario: Counts publish without an advertised endpoint

- **GIVEN** a replica with no advertised bridge endpoint holding 2 in-flight stream leases for account A
- **WHEN** its ring heartbeat fires
- **THEN** peers read account A with count 2 from that member
- **AND** endpoint-requiring membership reads still exclude the member

#### Scenario: Mixed-version metadata is missing, not zero

- **GIVEN** a ring member whose metadata was written by a version that does not publish counts
- **WHEN** a reader collects peer in-flight counts
- **THEN** that member is reported as having no published counts
- **AND** consumers that require counts from every member treat the data as incomplete
