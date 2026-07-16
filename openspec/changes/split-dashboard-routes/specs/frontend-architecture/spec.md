# frontend-architecture Delta

## ADDED Requirements

### Requirement: Dashboard routes are code-split

Each dashboard route's page component MUST load lazily so the entry chunk excludes the code of pages the operator has not visited; the built entry chunk MUST NOT statically import or modulepreload page chunks.

#### Scenario: Entry chunk excludes unvisited pages

- **WHEN** the dashboard entry page loads
- **THEN** only the visited route's page chunk is fetched
- **AND** the built entry chunk neither statically imports nor modulepreloads the other pages' chunks
