## 1. Scheduler behavior

- [x] 1.1 Add idle primary-window eligibility checks that require global enablement, per-account opt-in, active account state, a primary 5h window, and 0% used capacity.
- [x] 1.2 Deduplicate idle warm-up attempts per account/window/reset tuple.
- [x] 1.3 Stagger eligible idle attempts deterministically across the primary reset window.

## 2. Persistence and settings

- [x] 2.1 Add persistence for the staggered idle warm-up global setting.
- [x] 2.2 Add Alembic migration coverage for existing installs.
- [x] 2.3 Wire the setting through backend settings schemas, repository, service, and API.

## 3. Dashboard

- [x] 3.1 Add settings UI support for the global idle warm-up toggle.
- [x] 3.2 Surface per-account limit warm-up opt-in state on account cards.

## 4. Verification

- [x] 4.1 Add backend unit tests for idle eligibility, deduplication, and staggered scheduling.
- [x] 4.2 Add integration tests for settings and migrations.
- [x] 4.3 Add frontend tests for settings/account-card rendering.
