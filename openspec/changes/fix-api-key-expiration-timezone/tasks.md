# Tasks

- [x] Normalize `expires_at` to UTC naive in the API key service before create and update writes.
- [x] Add regression tests covering timezone-aware expiration datetimes for create and update flows.
- [x] Update the API key spec to state that ISO 8601 expiration datetimes with offsets are accepted and normalized before persistence.
