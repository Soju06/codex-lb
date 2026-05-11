## 1. Specs

- [ ] 1.1 Add API key enforcement requirements for service tier persistence and alias handling.
- [ ] 1.2 Validate OpenSpec changes.

## 2. Tests

- [ ] 2.1 Add unit coverage for service tier normalization and persistence.
- [ ] 2.2 Add integration coverage for dashboard CRUD, priority omission, and proxy enforcement.

## 3. Implementation

- [ ] 3.1 Add `enforced_service_tier` to DB/API/service layers.
- [ ] 3.2 Apply enforced service tier to proxied requests with `fast -> priority` normalization.
- [ ] 3.3 Add `omit_priority_request` to DB/API/UI layers.
- [ ] 3.4 Persist request-log priority omission metadata and render `but omitted` only on `Requested priority` rows.
