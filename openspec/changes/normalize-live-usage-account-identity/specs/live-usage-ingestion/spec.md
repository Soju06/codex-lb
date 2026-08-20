# live-usage-ingestion Delta

## ADDED Requirements

### Requirement: Live usage account attribution requires explicit identity proof

Live usage ingestion MUST attribute a snapshot to a persisted account row only
when the supplied internal account ID exactly matches that row, or when the
snapshot also supplies a ChatGPT account identity that resolves to exactly one
persisted account row. A workspace-local suffix, shard suffix, or other string
shape on an unknown internal account ID MUST NOT by itself justify stripping or
rewriting the ID.

When an exact internal account ID and a ChatGPT account identity both resolve
but identify different persisted account rows, the snapshot MUST be dropped
without failing the proxied request. The ingestor MUST NOT let cached ChatGPT
identity resolution attribute a snapshot to a row that no longer owns that
ChatGPT identity, and it MUST revalidate cached exact internal account matches
before using them for attribution.

When neither identity path resolves to exactly one persisted account row, the
snapshot MUST be dropped without failing the proxied request. After a raw
snapshot identity resolves to a persisted account row, duplicate coalescing
MUST use that resolved account ID so repeated live snapshots do not bypass the
per-account write interval. When a later snapshot supplies a ChatGPT account
identity, the ingestor MUST NOT skip that snapshot using a previously cached
raw-ID alias before revalidating the supplied ChatGPT identity. ChatGPT identity
lookups MUST use an indexed lookup path and only need to inspect enough rows to
distinguish a unique match from ambiguity.

#### Scenario: Hub-published snapshot resolves through ChatGPT identity

- **GIVEN** a proxy path publishes a live usage snapshot with a workspace-suffixed internal ID
- **AND** it also supplies a ChatGPT account identity that maps to exactly one persisted account
- **WHEN** the live usage ingestor persists the snapshot
- **THEN** the usage rows are written for the uniquely mapped persisted account

#### Scenario: Unproven suffix is dropped

- **GIVEN** a proxy path publishes a live usage snapshot with an internal ID that does not exactly match a persisted account row
- **AND** no unique ChatGPT account identity is supplied for that snapshot
- **WHEN** the live usage ingestor processes it
- **THEN** no usage rows are written for a guessed prefix account
- **AND** the proxied request is not failed

#### Scenario: Normalized aliases are coalesced

- **GIVEN** a raw snapshot identity has resolved to a persisted account row
- **WHEN** the same raw identity publishes an unchanged snapshot inside the write coalescing interval
- **THEN** the duplicate snapshot is skipped using the resolved account ID

#### Scenario: Conflicting identities are dropped

- **GIVEN** a proxy path publishes a live usage snapshot with an exact internal account ID for one persisted account
- **AND** it also supplies a ChatGPT account identity that uniquely maps to a different persisted account
- **WHEN** the live usage ingestor processes it
- **THEN** no usage rows are written for either conflicting account

#### Scenario: Moved ChatGPT identities are revalidated

- **GIVEN** a ChatGPT account identity previously resolved to one persisted account
- **AND** that ChatGPT account identity now belongs to a different persisted account
- **WHEN** a later snapshot uses the same ChatGPT account identity with an unknown internal account ID
- **THEN** the usage rows are written only for the current persisted account that owns the ChatGPT identity

#### Scenario: Supplied ChatGPT identity bypasses stale raw alias coalescing

- **GIVEN** a raw snapshot identity previously resolved to a persisted account row
- **AND** an unchanged later snapshot supplies the same raw identity with a ChatGPT account identity
- **WHEN** that ChatGPT account identity now resolves to a different persisted account row
- **THEN** the later snapshot is not skipped by the stale raw identity alias
- **AND** the usage rows are written only for the current persisted account that owns the ChatGPT identity

#### Scenario: Cached exact account matches are revalidated

- **GIVEN** an exact internal account ID previously resolved to a persisted account row
- **AND** that persisted account row has since been deleted or rewritten
- **WHEN** a later snapshot uses the same internal account ID
- **THEN** the ingestor revalidates the cached match before attribution
- **AND** the stale cached account ID is not used for a usage-history write
