## Purpose

Expose separately operated CLIProxyAPI model catalogs through CodexLB while preserving native subscription ownership and unavailable external identities.

## ADDED Requirements

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
