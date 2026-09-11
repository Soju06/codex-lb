## 1. Implementation

- [x] 1.1 Add `shm_size: 1gb` to the Compose `postgres` service.
- [x] 1.2 Raise default `database_pool_size` to 25 and
  `database_max_overflow` to 15, documenting the 80-connection /
  20-raw-slot budget at the setting definition.
- [x] 1.3 Regenerate `docs/reference/settings.md`.

## 2. Regression coverage

- [x] 2.1 Policy-test that the Compose `postgres` service pins `shm_size`.
- [x] 2.2 Update the settings default assertion to the new pool size.

## 3. Validation

- [x] 3.1 Run the compose, db-session, settings, and Helm artifact suites.
- [x] 3.2 Run strict OpenSpec validation for this change.
