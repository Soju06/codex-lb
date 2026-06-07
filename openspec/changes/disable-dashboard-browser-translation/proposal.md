## Why

Chrome's built-in Translate action and the Google Translate extension can mutate React-owned dashboard text nodes by injecting translation markup. That conflicts with React reconciliation and can freeze the SPA.

## What Changes

- Allow browser translation tools to translate dashboard text.
- Install a small startup guard around DOM `removeChild`/`insertBefore` so externally moved nodes are logged and ignored instead of crashing React reconciliation.

## Impact

- Keeps the dashboard from crashing if browser translation or another extension mutates React-owned DOM.
- Keeps the dashboard usable for operators affected by Google Translate injection.
- Does not add built-in localization; a first-party i18n UI remains a separate feature.
