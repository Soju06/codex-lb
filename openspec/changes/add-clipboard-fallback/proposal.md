## Why

Clipboard copy currently depends on `navigator.clipboard.writeText`, which can fail in non-secure contexts and inside focus-trapped dialogs. This causes copy actions to fail in real flows even when the UI shows copy controls.

## What Changes

- Add a shared clipboard utility fallback that uses `document.execCommand("copy")` when the Clipboard API is unavailable.
- Allow callers to provide a container element so fallback copy works inside dialog focus scopes.
- Update shared and OAuth copy buttons to pass dialog container context when present.
- Ensure pointer clicks on copy buttons do not leave the copy trigger focused after copy attempts.
- Add regression tests for secure copy, fallback copy, dialog-scoped fallback, and copy-button dialog behavior.

## Capabilities

### New Capabilities
- `clipboard-copy-fallback`: Shared frontend clipboard behavior for secure and non-secure contexts, including dialog-scoped fallback copy.

### Modified Capabilities
- `frontend-architecture`: OAuth dialog copy action requirements now include resilient clipboard fallback behavior.
- `api-keys`: API key created dialog copy button requirements now include resilient clipboard fallback behavior.

## Impact

- Affected code: `frontend/src/utils/clipboard.ts`, shared copy button components, OAuth dialog copy button wiring.
- Affected tests: clipboard utility tests and copy-button dialog interaction tests.
- No backend API or schema changes.
