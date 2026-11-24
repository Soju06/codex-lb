## 1. Schema

- [x] 1.1 Add the six transcript columns to `http_bridge_operations` with
  conservative defaults.
- [x] 1.2 Add the session/state/created and response/state indexes.
- [x] 1.3 Add ORM metadata for every column and index.

## 2. Pure helpers

- [x] 2.1 Implement output-item identity extraction and validation.
- [x] 2.2 Implement echo matching with provider-id and optional-status rules.
- [x] 2.3 Implement exact tool-call/output de-duplication with fail-closed
  conflict handling.

## 3. Verification

- [x] 3.1 Add focused unit tests, including malformed JSON-compatible types.
- [x] 3.2 Add migration upgrade/downgrade and final-head coverage.
- [x] 3.3 Run migration topology, Ruff, type, and focused migration checks.
