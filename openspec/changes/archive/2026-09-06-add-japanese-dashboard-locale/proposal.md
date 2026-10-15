## Why

Japanese operators currently fall back to English because the dashboard's
existing i18n system supports only English, Simplified Chinese, and Korean.
Add Japanese throughout the dashboard using the established locale mechanism.

## What Changes

- Add a complete Japanese (`ja`) translation bundle, including validation,
  accessible labels, dialogs, and client-side notification messages.
- Detect Japanese browser language tags and offer `日本語` in both language menus;
  preserve instant switching, saved preferences, and the document language.
- Format dates and times in Japanese, and localize the API-key expiry calendar.
- Keep English fallback, explicit date/time preferences, compact `K/M/B` units,
  and `$` USD amounts consistent with existing behavior.
- Add translation integrity checks, interaction regression tests, and desktop
  and mobile screenshots.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Extend runtime locale selection and translation
  coverage to Japanese, including locale-aware date presentation.

## Impact

Frontend locale resources, locale selection, date formatting, calendar rendering,
tests, and the existing Configuration documentation. No new dependencies, server
settings, API contracts, database changes, or deployment steps are required.
