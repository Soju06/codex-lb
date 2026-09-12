## ADDED Requirements

### Requirement: HTTP bridge and subscription overflow migration convergence

The migration graph MUST retain the merge joining
`20260906_000000_add_http_bridge_rebind_claim` and
`20260908_000000_add_subscription_overflow` without rewriting either revision.
The merge revision MUST NOT perform DDL or mutate application rows.
The graph MUST have one final head joining that merge with
`20260908_000000_replace_upstream_stream_transport_default_sentinel`.
This subsequent merge MUST also be metadata-only and preserve both histories.
When upstream independently merges overflow and transport, a final metadata-only
merge MUST join that upstream merge and the recovery merge without rewriting
any previous revision.

#### Scenario: Upgrade from either existing branch

- **GIVEN** a database at either parent of either merge with existing application rows
- **WHEN** it upgrades to the merged head
- **THEN** both branch schemas exist, schema drift is absent, and existing rows are preserved

#### Scenario: Reverse and reapply the merge

- **GIVEN** a database at the merged head
- **WHEN** only the merge revision is downgraded and reapplied
- **THEN** both branch schemas and all application rows remain unchanged
