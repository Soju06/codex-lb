## 1. Backend settings and persistence

- [x] 1.1 Add `dashboard_settings.weekly_pace_working_days` with all-days default and migration.
- [x] 1.2 Wire the setting through settings repository, service, API schemas, and responses.
- [x] 1.3 Include the setting in settings audit `changed_fields`.

## 2. Weekly pace behavior

- [x] 2.1 Parse configured working days for dashboard projections.
- [x] 2.2 Apply working days to scheduled-by-now, pace gap, and scheduled burn-rate math.

## 3. Frontend

- [x] 3.1 Add frontend settings schema support for `weeklyPaceWorkingDays`.
- [x] 3.2 Add a settings UI control for selecting weekly pace working days.

## 4. Verification

- [x] 4.1 Add backend integration tests for settings round-trip and invalid working-day values.
- [x] 4.2 Add backend integration coverage proving working days alter weekly pace projections.
- [x] 4.3 Add settings audit coverage for the new field.
- [x] 4.4 Add frontend schema and settings-control tests.
