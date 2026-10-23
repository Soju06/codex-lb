## ADDED Requirements

### Requirement: Native HTTP tool resends preserve owner-bound context
The HTTP Responses bridge MUST recognize a complete native tool-loop resend on its durable owner when the stored input count and fingerprint match and the response-bound pending-call manifest is exactly settled by matching direct calls and outputs. The classification MUST retain duplicate, unknown-manifest, incomplete-batch and malformed-item rejection. Native item metadata MUST be an object to qualify for this additional proof. Shape-valid developer controls in the verified historical prefix and completed shape-valid assistant commentary before the tool batch MUST NOT prevent this proof. Fresh developer controls and agent-message suffixes MUST NOT gain permission through this proof.

A qualifying fresh bridge MUST send the complete original request on its durable account without a stale injected response anchor. The bridge MUST preserve input, reasoning, compaction, metadata, instructions and prompt cache identity through the existing wire serializer. The verification view MUST NOT replace the transmitted body or authorize account-neutral replay. Implicit preferred-account fallback and owner retirement MUST NOT send an owner-bound request to another account. Conflicting file or turn-state ownership MUST retain existing fail-closed behavior.

Stale-anchor recovery using this proof MUST require the matching durable owner, unchanged payload and durable proof, an operation fence, no previously visible response output, and no prior replay. It MUST attempt at most one unanchored replay on the same account.

#### Scenario: Fresh bridge receives complete native tool history
- **GIVEN** a complete tool-loop resend matches the durable prefix and exact response-bound call manifest
- **WHEN** native metadata or completed assistant commentary precedes its matching tool results and no reusable bridge remains
- **THEN** the original complete input is sent once on the durable account without the stale anchor
- **AND** its cache key and instructions are preserved

#### Scenario: Stale anchor is rejected before visible output
- **GIVEN** the complete native tool history has an unchanged durable owner-bound proof
- **WHEN** its anchor is rejected before visible output and a valid unreplayed operation fence exists
- **THEN** recovery sends the original complete request without the anchor once on that same owner

#### Scenario: Alternate account cannot receive owner-bound context
- **GIVEN** the durable owner of a qualifying native tool resend is unavailable and another account is selectable
- **WHEN** ordinary selection or quarantined-anchor recovery runs
- **THEN** the request fails closed without sending the history to the alternate account

#### Scenario: Incomplete or malformed history does not qualify
- **WHEN** the prefix changes, the manifest is unknown, a parallel call or output is absent, an output is duplicated, commentary or metadata is malformed, or fresh developer control is inserted
- **THEN** the additional owner-bound proof is rejected

#### Scenario: Existing replay fences remain effective
- **WHEN** the operation fence is missing, a replay already occurred, output is visible, or the durable owner no longer matches
- **THEN** the native tool proof does not authorize an unanchored replay
