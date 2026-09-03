## Why

The account import dialog currently retains only the first selected `auth.json` file, forcing operators to repeat the same workflow for every account. Operators need to select and import multiple exported account files in one interaction while preserving the backend's bounded one-file request contract.

## What Changes

- Allow the account import file picker to select multiple JSON files and retain the complete selection.
- Import selected files one at a time through the existing `POST /api/accounts/import` endpoint, stopping and keeping the dialog open if any import fails.
- Close and reset the dialog only after every selected file succeeds.
- Update localized import instructions and add focused frontend regression coverage for multi-file selection, sequential submission, success, and failure.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `frontend-architecture`: The Accounts import flow accepts a multi-file selection and imports every selected `auth.json` file through the existing single-file API contract.

## Impact

Dashboard SPA only: the Accounts import dialog, its page integration, localized copy, and focused Vitest coverage. The backend API, multipart limits, database, dependencies, and account identity/import semantics remain unchanged.
