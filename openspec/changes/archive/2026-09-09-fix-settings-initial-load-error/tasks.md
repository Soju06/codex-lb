## 1. Regression coverage

- [x] 1.1 Add a failing `SettingsPage` test asserting the initial fetch error message and Retry action
- [x] 1.2 Add a test that a pending first load still renders the skeleton and no error
- [x] 1.3 Add a test that a fetch error with cached settings keeps the form visible
- [x] 1.4 Add a test that Retry refetches the settings query and is disabled while fetching

## 2. Settings first-load error state

- [x] 2.1 Split the `SettingsPage` skeleton branch into pending-skeleton and failed-load branches
- [x] 2.2 Render the failed-load error through `AlertMessage` inside an alert semantic with a Retry `Button`
- [x] 2.3 Drop `settingsQuery.error` from the page-level aggregate alert when no settings are loaded
- [x] 2.4 Add `settings.toasts.loadFailed` to `en` / `ko` / `zh-CN`

## 3. Validation

- [x] 3.1 `npm test -- src/features/settings/components/settings-page.test.tsx`
- [x] 3.2 `npm run lint`, `npm run typecheck`, `npm run build`
- [x] 3.3 `openspec validate fix-settings-initial-load-error --strict`
- [x] 3.4 Browser-verify a failed `GET /api/settings` shows the error and Retry instead of the skeleton
