# Sticky Session Operations Specification

## Purpose

Define how sticky-session mappings are typed, managed, and cleaned up so bounded prompt-cache affinity remains operationally controllable without affecting durable routing modes.

## Requirements
### Requirement: Sticky sessions are explicitly typed and provider-scoped
The system SHALL persist each sticky-session mapping with an explicit kind and provider-scoped routing identity so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed without assuming every mapping targets a ChatGPT account.

Each persisted mapping MUST use provider scope as part of its durable identity. After the provider-scoped migration, persisted sticky mappings MUST be uniquely identified by provider scope, sticky kind, and sticky key, and each row MUST contain a non-empty `routing_subject_id`.

#### Scenario: Backend Codex session affinity is stored as durable
- **WHEN** a backend Codex request creates or refreshes stickiness from `session_id`
- **THEN** the stored mapping kind is `codex_session`

#### Scenario: Dashboard sticky thread routing is stored as durable
- **WHEN** sticky-thread routing creates or refreshes stickiness from a prompt-derived key
- **THEN** the stored mapping kind is `sticky_thread`

#### Scenario: OpenAI prompt-cache affinity is stored as bounded
- **WHEN** an OpenAI-style request creates or refreshes prompt-cache affinity
- **THEN** the stored mapping kind is `prompt_cache`

#### Scenario: Identical keys remain isolated across sticky-session kinds
- **WHEN** the same sticky-session key value is used for more than one kind
- **THEN** each `(key, kind)` mapping is stored and managed independently without overwriting the others

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

### Requirement: Dashboard exposes sticky-session administration
The system SHALL provide dashboard APIs for listing sticky-session mappings, deleting one mapping, and purging stale mappings.

#### Scenario: List sticky-session mappings
- **WHEN** the dashboard requests sticky-session entries
- **THEN** the response includes each mapping's `key`, `account_id`, `kind`, `created_at`, `updated_at`, `expires_at`, and `is_stale`
- **AND** the response includes the total number of stale `prompt_cache` mappings that currently exist beyond the returned page

#### Scenario: List only stale mappings
- **WHEN** the dashboard requests sticky-session entries with `staleOnly=true`
- **THEN** the system applies stale prompt-cache filtering before enforcing the result limit

#### Scenario: Delete one mapping
- **WHEN** the dashboard deletes a sticky-session mapping by both `key` and `kind`
- **THEN** the system removes that mapping and returns a success response

#### Scenario: Purge stale prompt-cache mappings
- **WHEN** the dashboard requests a stale purge
- **THEN** the system deletes only stale `prompt_cache` mappings and leaves durable mappings untouched

### Requirement: Prompt-cache mappings are cleaned up proactively
The system SHALL run a background cleanup loop that deletes stale `prompt_cache` mappings using the current dashboard prompt-cache affinity TTL.

#### Scenario: Cleanup loop removes stale prompt-cache mappings
- **WHEN** the cleanup loop runs and finds `prompt_cache` mappings older than the configured TTL
- **THEN** it deletes those mappings

#### Scenario: Cleanup loop preserves durable mappings
- **WHEN** the cleanup loop runs
- **THEN** it does not delete `codex_session` or `sticky_thread` mappings regardless of age
