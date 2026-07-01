## 1. Specification

- [x] 1.1 Add `fleet-summary` OpenSpec capability delta for summary and refresh behavior.

## 2. Backend API

- [x] 2.1 Add a minimal fleet summary response shape.
- [x] 2.2 Add API-key-authenticated `GET /api/fleet/summary`.
- [x] 2.3 Add API-key-authenticated `POST /api/fleet/refresh`.
- [x] 2.4 Reuse existing usage refresh machinery and preserve refresh policy rules.
- [x] 2.5 Register the fleet router.

## 3. Tests

- [x] 3.1 Add integration tests for missing/invalid API key rejection.
- [x] 3.2 Add integration tests for minimal projection and sensitive-field omission.
- [x] 3.3 Add integration tests for bounded refresh response shape.
