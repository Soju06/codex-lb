## ADDED Requirements

### Requirement: Explicit compact requests route to Responses model sources

The system MUST route explicit compact requests on `/v1/responses/compact` and `/backend-api/codex/responses/compact` to the selected Responses model source's `/responses/compact` endpoint when subscription continuity or uploaded-file ownership does not require native routing. Source selection MUST preserve API-key source scope, model enforcement and native registry precedence. Disabled source ownership MUST produce the existing disabled-source error rather than subscription fallback.

#### Scenario: External compact works without subscription accounts
- **GIVEN** an enabled Responses source serves an external model and no subscription accounts exist
- **WHEN** the client posts a compact request for that model on either explicit compact route
- **THEN** the source receives the compact request authenticated with its configured source credential
- **AND** the client receives its compact result without a subscription account lookup failure

#### Scenario: Native continuity remains native
- **GIVEN** a compact request includes a subscription-owned previous response, turn-state alias or uploaded file
- **WHEN** a Responses source also claims the requested model
- **THEN** the source receives no request
- **AND** the existing subscription compact reconciliation determines the result

#### Scenario: A disabled source does not fall through
- **WHEN** a compact request selects an external source or source model that is disabled
- **THEN** the client receives `503 model_source_disabled`
- **AND** neither source nor subscription upstream receives the request

### Requirement: External compact preserves source protocol and dispatch ownership

External compact forwarding MUST retain the supplied history and opaque source compaction state without subscription-specific trimming or tool removal. It MUST apply API-key policy and source telemetry filtering, use the source's configured authentication and bounded transport, and retain the existing source admission, usage settlement and cancellation guarantees. Admission MUST estimate usage from the actual source-bound payload, including retained tool definitions. Source failures MUST remain explicit without subscription or model fallback. The SDK route MUST retain the source compact envelope; the Codex route MUST apply its existing compact output normalization.

#### Scenario: Source compact history is retained
- **WHEN** external compact input contains messages, completed tool pairs and opaque source compaction state
- **THEN** the source receives that history without silent removal
- **AND** source compaction content is returned unchanged within the applicable public envelope

#### Scenario: Limited-key compact usage settles
- **WHEN** a source compact response reports usage for a limited API key
- **THEN** the source dispatch settles the reservation using that usage before reporting success
- **AND** a later request can acquire the released source admission capacity

#### Scenario: Preserved definitions count toward admission
- **WHEN** a compact request's retained tool definitions fill the key's remaining bounded reservation budget
- **THEN** that budget remains reserved until the request settles
- **AND** a concurrent request cannot consume the same remaining quota

#### Scenario: Unsupported compact remains an explicit source failure
- **WHEN** the selected source rejects `/responses/compact`
- **THEN** the client receives the source error with credential redaction and applicable retry metadata
- **AND** no subscription fallback occurs

#### Scenario: Cancellation releases source ownership
- **WHEN** the client disconnects or compact dispatch is cancelled
- **THEN** the source dispatcher releases its connection, admission claim and reservation through its existing cleanup lifecycle
