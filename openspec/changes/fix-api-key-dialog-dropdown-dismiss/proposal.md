## Why

The API-key create and edit dialogs contain portalled model and account dropdowns. Interacting with a dropdown item is currently treated as an outside interaction by the parent dialog, which dismisses the dialog before the operator can save the changed selection.

## What Changes

- Keep API-key dialogs open while the operator interacts with their portalled dropdown content.
- Preserve normal click-outside dismissal for interactions that do not originate in owned dropdown content.
- Add product-path regression coverage for selecting models in the edit dialog and then saving the resulting allowlist.

## Impact

- Dashboard-only behavior change in API-key create/edit dialogs.
- No API, database, authentication, or routing contract changes.
- Existing keyboard and ordinary outside-click dismissal behavior remains in scope for regression verification.
