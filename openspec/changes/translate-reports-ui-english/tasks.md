## 1. Spec

- [x] 1.1 Add a `frontend-architecture` delta for English `/reports` user-facing labels.

## 2. Reports UI

- [x] 2.1 Update page-owned `/reports` labels to the accepted English wording set for the current reports surface.
- [x] 2.2 Keep `/reports` loading report data from `GET /api/reports`.
- [x] 2.3 Keep visible `/reports` filter controls for start date, end date, account, and model.
- [x] 2.4 Keep `/api/reports` requests from `/reports` using `startDate`, `endDate`, `accountId`, and `model` parameter names.
- [x] 2.5 Keep backend-provided strings, account/model values, and raw server error payload text out of scope unless `/reports` wraps them with page-owned labels.

## 3. Verification

- [x] 3.1 Confirm the default `/reports` view renders these exact page-owned labels: `Cost Report`, `Usage history by date range`, `Total Cost`, `Requests`, `Cost by Day`, `Tokens by Day`, `Distribution by Model`, `Daily Breakdown`, `Day`, `Input Tokens`, `Output Tokens`, `Cost`, and `Accounts`.
- [x] 3.2 Confirm `/reports` state labels render these exact page-owned labels when each state is triggered: `Loading...`, `Failed to load report data:`, `Failed to load model options:`, `Failed to load account options:`, `Some report data could not be loaded. Try reloading.`, and `Retry`.
- [x] 3.3 Confirm opening `/reports` loads data from `GET /api/reports`.
- [x] 3.4 Confirm `/reports` exposes visible filter controls for start date, end date, account, and model.
- [x] 3.5 Confirm changing the start date, end date, account, and model controls refetches `/api/reports` with `startDate`, `endDate`, `accountId`, and `model` parameter names.
- [x] 3.6 Confirm untranslated backend-provided strings, account/model values, and raw server error payload text are not introduced as required English copy unless the page renders its own label around them.
- [x] 3.7 Run `openspec validate translate-reports-ui-english --strict`.
