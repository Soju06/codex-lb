## Purpose

Expose separately operated CLIProxyAPI model catalogs through CodexLB while preserving native subscription ownership and unavailable external identities.

## Requirements

### Requirement: Automatic external discovery

The system SHALL support an opt-in CLIProxyAPI catalog mode on an existing Responses-capable model source. The system SHALL acquire its Codex-shaped catalog using that source's inference URL and encrypted inference credential. Public and Codex catalog requests SHALL expose discovered external models without requiring manual model entries. Existing manually configured sources SHALL retain their behavior.

#### Scenario: Configure once

- **WHEN** an operator enables CPA catalog discovery and CPA lists a new external model
- **THEN** a subsequent catalog refresh SHALL expose that model in the public and Codex model catalogs without a second manual model list
- **AND** the system SHALL NOT expose source credentials or internal source identifiers in those catalogs

### Requirement: Preserve native routing

Native subscription model identities SHALL retain precedence over discovered external identities. CPA discovery SHALL NOT create, replace, or authenticate native subscription accounts.

#### Scenario: Catalog collision

- **WHEN** CPA lists a model with the same identity as a native subscription model
- **THEN** the default catalog and request routing SHALL retain the native subscription model

### Requirement: Durable outage cache

The system SHALL retain the last successfully validated catalog when discovery fails due to transport, authentication, status, invalid JSON, or invalid catalog structure. Refreshes SHALL have a five-second total acquisition budget and bounded body size. Each discovered model SHALL provide a positive context limit that fits persisted model metadata. Invalid display names or capacity values SHALL invalidate the complete snapshot. Discovery failures SHALL NOT remove native catalog entries or silently substitute models.

#### Scenario: CPA is unavailable

- **WHEN** a previously successful catalog refresh is followed by an unavailable CPA endpoint
- **THEN** the previously discovered list SHALL remain available
- **AND** a request to an unavailable external model SHALL return an explicit failure without selecting a different model

#### Scenario: Invalid catalog

- **WHEN** CPA returns a malformed successful response
- **THEN** the previous complete catalog SHALL remain intact without partially applying that response

### Requirement: Retain unavailable identity

On successful refresh, omitted external models SHALL leave new selections while their source ownership SHALL remain stored. Requests for omitted models SHALL fail explicitly without native fallback. Reappearing models SHALL become selectable automatically. Discovery SHALL NOT delete conversation history.

#### Scenario: Successful omission and return

- **WHEN** a model disappears from a successful CPA catalog
- **THEN** catalog endpoints SHALL omit it and Responses requests SHALL return an explicit unavailable error
- **AND** when a later successful catalog includes it again it SHALL become selectable with its retained identity

### Requirement: Metadata and workflow fidelity

The system SHALL translate CPA model identity, display name, context limit, output limit, modalities, and reasoning metadata into the existing catalog contract. It SHALL NOT treat inherited metadata as verified provider capability. The bridge SHALL preserve supported Responses workflow fields without silently dropping custom tools, deferred discovery, or continuation inputs. Unknown actual provider capability SHALL remain an explicit verification limitation.

#### Scenario: Discovered metadata

- **WHEN** CPA returns model metadata
- **THEN** CodexLB SHALL translate that metadata without leaking credentials or internal request overrides
- **AND** provider capability claims SHALL be distinguished from independently verified behavior in the acceptance evidence

### Requirement: Refresh ownership

A refresh SHALL NOT overwrite a source configuration changed or deleted during its upstream request. Concurrent refreshes SHALL NOT replace newer accepted state with stale results.

#### Scenario: Source changes during acquisition

- **WHEN** an operator changes or deletes the source while a refresh is acquiring its catalog
- **THEN** the completed acquisition SHALL NOT apply to the changed or deleted configuration


### Requirement: Refresh failure isolation

Catalog reads SHALL continue to stored data after refresh enumeration, claim or apply database failures when the independent catalog read remains available. Refresh work, including source enumeration, SHALL fit the five-second acquisition budget. Acquisition SHALL use at most four concurrent workers and SHALL start eligible waiting sources when a worker becomes available. One source failure SHALL NOT cancel another source's refresh. Caller cancellation SHALL cancel and await owned refresh tasks.

#### Scenario: One source stalls

- **WHEN** one source stalls while other acquisitions finish and more sources are waiting
- **THEN** waiting sources SHALL use the released capacity without waiting for the stalled source

#### Scenario: Refresh database failure

- **WHEN** refresh work fails but the independent stored catalog read succeeds
- **THEN** the catalog endpoint SHALL return stored models

### Requirement: Mode transition ownership

When catalog mode changes without an explicit replacement model list, previous model rows SHALL become disabled atomically with the mode change. Their identities SHALL remain stored to prevent stale requests from falling through to native routing. A successful CPA acquisition SHALL restore returned identities in place.

#### Scenario: Change catalog mode

- **WHEN** an operator changes a populated source between manual and CPA mode without replacement models
- **THEN** previous models SHALL leave catalogs and stale requests SHALL return an explicit unavailable error

### Requirement: Optional reasoning descriptions

CPA reasoning entries SHALL accept absent or null descriptions. The Codex catalog SHALL use the effort string as the description for those entries.

#### Scenario: Effort without description

- **WHEN** CPA supplies a reasoning effort without a description
- **THEN** the Codex catalog SHALL retain the effort and expose its effort string as the description

### Requirement: CPA allowed-tool aliases

CPA Responses forwarding SHALL normalize supported aliases in nested allowed-tool choices while preserving custom and namespace tool declarations.

#### Scenario: Nested web search alias

- **WHEN** a CPA request selects web_search_preview inside allowed_tools
- **THEN** the forwarded choice SHALL use web_search and preserve the other allowed tools

### Requirement: Dashboard identity migration convergence

CPA catalog and dashboard identity migration histories SHALL converge through an append-only merge revision without changing published revisions. Upgrades SHALL preserve existing CPA source identities, encrypted source keys, catalog state, retention settings and guest generation. Existing role rows, grants and user identities and credentials SHALL remain intact after their identity parent has completed. Upgrades from older histories SHALL apply the existing identity migrations' documented legacy credential conversion and audit timestamp normalization. Audit history and existing actor/target attribution SHALL remain intact.

#### Scenario: Populated independent histories

- **WHEN** a database starts at the published CPA/guest head, the dashboard identity head, or both parent stamps
- **THEN** upgrading to head SHALL produce one head with both schemas and their preserved data

#### Scenario: Merge-only downgrade

- **WHEN** the merge is downgraded to either immediate parent
- **THEN** both parent stamps and both schemas SHALL remain without deleting or changing stored data
- **AND** re-upgrading SHALL restore the single merge stamp without schema drift

### Requirement: Dashboard invitation migration convergence

CPA catalog and dashboard invitation histories SHALL converge through an append-only merge without changing published revisions. Existing invitations, token hashes, user and creator identities, expiry and consumption/revocation state SHALL remain intact, together with source keys/catalogs, roles/grants, users/credentials, API key ownership and audit history.

#### Scenario: Populated independent parents

- **WHEN** a database at either parent or both stamps upgrades to head
- **THEN** the database SHALL contain both schemas at one head without changing previously applied data
- **AND** a database without invitations SHALL gain an empty invitation table

#### Scenario: Merge-only downgrade and re-upgrade

- **WHEN** the merge is downgraded to either immediate parent and upgraded again
- **THEN** downgrade SHALL restore both parent stamps without dropping schemas or data
- **AND** re-upgrade SHALL restore one head without schema drift
