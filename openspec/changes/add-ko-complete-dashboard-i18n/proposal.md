## Why

The dashboard already has a runtime i18n foundation and partial Simplified
Chinese coverage, but most feature pages still render English-only strings.
Korean operators should be able to run the dashboard in Korean, and Chinese
operators should not hit untranslated feature surfaces once they leave the
header/auth/settings paths covered by the original i18n work.

## What Changes

- Add Korean (`ko`) as a supported dashboard locale.
- Extend Simplified Chinese coverage to the remaining dashboard feature
  surfaces that still render hard-coded English copy.
- Route user-visible feature labels, empty states, dialogs, table headings,
  button labels, accessible labels, and toast fallback copy through i18n.
- Keep product names, protocol names, model/API terms, and short operational
  abbreviations in English where translating them would read less naturally.

## Impact

- Frontend-only behavior change.
- Existing English copy remains the default and the fallback locale.
- No server API, database schema, or proxy behavior changes.
