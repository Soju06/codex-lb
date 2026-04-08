## MODIFIED Requirements

### Requirement: Sticky sessions are explicitly typed and provider-scoped

The system SHALL persist each sticky-session mapping with an explicit kind and provider-scoped routing identity so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed without assuming every mapping targets a ChatGPT account.

Each persisted mapping MUST use provider scope as part of its durable identity. After the provider-scoped migration, persisted sticky mappings MUST be uniquely identified by provider scope, sticky kind, and sticky key, and each row MUST contain a non-empty `routing_subject_id`.

#### Scenario: Platform prompt-cache affinity is stored against a provider-scoped routing target

- **WHEN** an OpenAI-style stateless request creates or refreshes prompt-cache affinity through an `openai_platform` upstream
- **THEN** the stored mapping references the selected provider-scoped routing target
- **AND** it does not require a `chatgpt_account_id`

#### Scenario: Identical sticky keys remain isolated across providers

- **WHEN** the same sticky-session key value is used by both `chatgpt_web` and `openai_platform`
- **THEN** the stored mappings remain isolated by provider scope and kind
- **AND** one provider's refresh or cleanup activity does not overwrite the other's mapping

#### Scenario: Sticky lookup and deletion remain provider-scoped

- **WHEN** the service looks up, deletes, or bulk-deletes a sticky-session mapping
- **THEN** it scopes that operation by provider scope, sticky kind, and sticky key
- **AND** it MUST NOT reuse or remove a mapping belonging to another provider with the same sticky key

#### Scenario: Platform codex-session persistence is rejected

- **WHEN** a request would persist a `codex_session` mapping for `openai_platform`
- **THEN** the service rejects or skips that persistence path
- **AND** it MUST NOT store a durable Platform `codex_session` row in phase 1

#### Scenario: ChatGPT durable continuity remains provider-scoped

- **WHEN** a durable `codex_session` mapping is created from ChatGPT-web session continuity
- **THEN** that mapping remains eligible only for `chatgpt_web` routing decisions
- **AND** it is not reused for `openai_platform` requests

#### Scenario: Existing ChatGPT sticky mappings are backfilled with explicit provider scope

- **WHEN** the rollout introduces provider-scoped sticky persistence
- **THEN** existing legacy sticky mappings are backfilled or interpreted as `chatgpt_web`
- **AND** they remain valid only for ChatGPT-web routing decisions
- **AND** each migrated row gets `routing_subject_id = account_id`

#### Scenario: Ambiguous legacy sticky mappings fail closed during rollout

- **WHEN** a legacy sticky mapping cannot be deterministically associated with a single provider scope after the schema change
- **THEN** the service invalidates, drops, or ignores that mapping instead of reusing it
- **AND** it MUST NOT reuse that mapping across provider kinds
