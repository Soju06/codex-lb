## 1. Specification

- [x] 1.1 Create OpenSpec proposal, tasks, and delta specs.

## 2. Persistence and Domain Model

- [x] 2.1 Add model-source ORM models for OpenAI-compatible sources and source models.
- [x] 2.2 Add API-key model-source assignment persistence without changing account assignments.
- [x] 2.3 Add Alembic migration with idempotent table/index creation for SQLite and PostgreSQL.

## 3. Backend Source Management

- [x] 3.1 Add typed schemas, repository, service, and DI context for model-source management.
- [x] 3.2 Validate source URLs, model metadata, duplicate slugs per source, and encrypted upstream API keys.
- [x] 3.3 Add admin API routes for listing, creating, updating, enabling/disabling, and deleting sources.
- [x] 3.4 Add dashboard controls for creating, enabling/disabling, and deleting OpenAI-compatible sources.

## 4. Model Catalog

- [x] 4.1 Add source identity to model registry entries while preserving subscription defaults.
- [x] 4.2 Include enabled OpenAI-compatible source models in `/v1/models`.
- [x] 4.3 Include Responses-capable source models in `/backend-api/codex/models` for Codex model-picker discovery.

## 5. API Key Scoping and Usage

- [x] 5.1 Add `assigned_source_ids` to API key create/update/read contracts.
- [x] 5.2 Enforce API-key source scope during model listing and request routing.
- [x] 5.3 Reuse existing API-key reservation settlement with upstream OpenAI `usage` for source-routed requests.
- [x] 5.4 Fail closed when a source-routed response lacks required usage and the key has token/cost limits.
- [x] 5.5 Add dashboard API-key source assignment controls separate from account assignment controls.

## 6. Public OpenAI-Compatible Routing

- [x] 6.1 Route `/v1/chat/completions` requests for source-owned models to the selected OpenAI-compatible source.
- [x] 6.2 Route `/v1/responses` only when the selected source explicitly supports Responses-compatible requests.
- [x] 6.3 Route `/backend-api/codex/responses` only when the selected source explicitly supports Responses-compatible requests.
- [x] 6.4 Route `/v1/audio/transcriptions` to OpenAI-compatible sources that explicitly support audio transcriptions.
- [x] 6.5 Preserve subscription routing for Codex-native compaction, file, control-plane, websocket, and incompatible source paths.

## 7. Observability

- [x] 7.1 Add request-log source fields or equivalent diagnostics for source kind/source id.
- [x] 7.2 Ensure logs never include upstream source API key material.

## 8. Tests and Validation

- [x] 8.1 Add unit tests for registry source metadata and source service validation.
- [x] 8.2 Add integration tests for `/v1/models` source filtering by API key.
- [x] 8.3 Add integration tests for source-routed usage settlement.
- [x] 8.4 Add integration tests for Codex model-picker source discovery and `/backend-api/codex/responses` source routing.
- [x] 8.5 Add integration tests for source-routed audio transcription multipart forwarding and usage settlement.
- [x] 8.6 Add frontend schema/component tests for model-source management and API-key source assignment.
- [x] 8.8 Add tests for duration-based audio billing (per-minute rate, cost settlement, zero-token limit interaction).
- [x] 8.7 Run targeted tests and `openspec validate --specs` when the CLI is available.
