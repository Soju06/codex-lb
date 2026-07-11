## 1. OpenSpec

- [x] 1.1 Add requirements for configurable pre-reset warm-up exhaustion.
- [x] 1.2 Add persistence and dashboard-control requirements for the setting.

## 2. Backend

- [x] 2.1 Add `dashboard_settings.limit_warmup_exhausted_threshold_percent` with a 99.0 default and migration.
- [x] 2.2 Wire the setting through settings repository, service, API schemas, responses, and audit changed-fields.
- [x] 2.3 Use the threshold in limit warm-up candidate selection.

## 3. Frontend

- [x] 3.1 Add frontend settings schema and payload support.
- [x] 3.2 Add a settings UI input for the exhausted threshold.

## 4. Verification

- [x] 4.1 Add backend regression coverage for a 99 percent pre-reset sample.
- [x] 4.2 Add backend settings and migration coverage.
- [x] 4.3 Add frontend schema, payload, and settings-control coverage.
