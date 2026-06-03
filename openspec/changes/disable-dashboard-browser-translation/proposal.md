## Why

Chrome's built-in Translate action and the Google Translate extension can mutate React-owned dashboard text nodes by injecting translation markup. That conflicts with React reconciliation and can freeze the SPA.

## What Changes

- Mark the dashboard HTML document, body, and React root as non-translatable.
- Add Google's `notranslate` meta tag so browser translation tools do not rewrite dashboard DOM text nodes.

## Impact

- Prevents browser translation from modifying the live React dashboard DOM.
- Keeps the dashboard usable for operators affected by Google Translate injection.
- Does not add built-in localization; a first-party i18n UI remains a separate feature.
