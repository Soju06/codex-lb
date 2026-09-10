## ADDED Requirements

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
