## 1. Contract and persistence

- [x] 1.1 Add nullable `allowed_reasoning_efforts` persistence and a reversible migration from the current Alembic head.
- [x] 1.2 Extend API-key request/response schemas, service data, repository updates, and cache-facing mapping with normalized list semantics.
- [x] 1.3 Validate non-empty supported values and mutual exclusion with `enforced_reasoning_effort` on create and effective PATCH state.

## 2. Proxy policy

- [x] 2.1 Add a typed OpenAI-compatible permission error and enforce the allowlist in the shared Responses policy before wire normalization, including accepted aliases.
- [x] 2.2 Prove the shared policy covers Responses, compact, WebSocket, Chat Completions, aliases, and source-routed chat without reservation or upstream dispatch for rejected requests.

## 3. Dashboard

- [x] 3.1 Add a localized accessible multi-select to API-key create/edit dialogs, preserving unrestricted `null` behavior and clearing the conflicting fixed-effort control.
- [x] 3.2 Update frontend API schemas and tests for list serialization, validation, and existing-key compatibility.

## 4. Verification

- [x] 4.1 Add focused backend unit and integration tests, including migration upgrade/downgrade and dashboard API effective-state validation.
- [x] 4.2 Run focused frontend tests/build, relevant backend checks, migration graph checks, and OpenSpec validation.
- [x] 4.3 Capture before/after dashboard screenshots and include them with the linked issue and PR test plan.
