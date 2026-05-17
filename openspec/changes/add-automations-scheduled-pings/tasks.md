## 1. Specs

- [x] 1.1 Add `automations` capability requirements for job CRUD, scheduling semantics, failover, and run history.
- [x] 1.2 Add `frontend-architecture` requirements for `Automations` navigation and page workflows.
- [x] 1.3 Validate OpenSpec artifacts.
- [x] 1.4 Align spec/context wording with implemented all-accounts semantics, weekday schedule, threshold window, and reasoning effort fields.

## 2. Backend

- [x] 2.1 Add DB models + migrations for automation jobs, job-account bindings, and run history rows.
- [x] 2.2 Implement `app/modules/automations` (`schemas.py`, `repository.py`, `service.py`, `api.py`) and dependency wiring.
- [x] 2.3 Implement scheduler loop with leader election and due-run claiming that guarantees one run per schedule slot across replicas.
- [x] 2.4 Implement ping execution + account failover policy for retryable account errors (rate-limit/quota/deactivated/auth failures).
- [x] 2.5 Persist run outcomes (`success`, `failed`, `partial`) with error code/message for GUI visibility.
- [x] 2.6 Implement `run-now` endpoint execution path reusing the same run persistence/failover engine.

## 3. Frontend

- [x] 3.1 Add `/automations` route and header navigation item.
- [x] 3.2 Build `AutomationsPage` with job list, create/edit form, enable toggle, and delete action.
- [x] 3.3 Reuse existing account/model selectors in the automation form.
- [x] 3.4 Add run-history panel/table showing latest status and failure details per job.

## 4. Tests

- [x] 4.1 Add backend unit tests for schedule computation (timezone + DST boundaries) and failover classification.
- [x] 4.2 Add backend integration tests for CRUD APIs, run claiming, and multi-account fallback execution.
- [x] 4.3 Add frontend tests for navigation, form validation, create/update flows, and run-history rendering.
- [x] 4.4 Run full verification suite (`uv run pytest`, `uvx ruff check .`, `uv run ty check`).
- [x] 4.5 Run frontend test suite (`cd frontend && bun run test`).
