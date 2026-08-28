## 1. Settings contract and persistence

- [x] 1.1 Add the disabled-by-default `prohibit_fast_mode` dashboard setting to the ORM, migration, settings repository, service, and API schemas.
- [x] 1.2 Expose `prohibitFastMode` in the frontend settings schemas and update payload builder.
- [x] 1.3 Add the routing-settings toggle and localized copy.

## 2. Fast Mode alias enforcement

- [x] 2.1 Pass the dashboard policy into model-alias normalization so `-fast` aliases retain their base model and reasoning but do not derive priority.
- [x] 2.2 Apply the policy before source selection, quota accounting, and OpenAI forwarding for HTTP and Codex WebSocket request paths.

## 3. Verification and documentation

- [x] 3.1 Add backend settings, request-policy, and Codex request-path regression tests.
- [x] 3.2 Add frontend schema and routing-settings UI regression tests.
- [x] 3.3 Update stable OpenSpec context and run focused tests, lint/type checks, migration checks, and strict OpenSpec validation.
