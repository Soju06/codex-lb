## 1. Spec and Locale Support

- [x] Add OpenSpec coverage for Korean locale support and complete dashboard i18n.
- [x] Register `ko` in the frontend i18n bootstrap and language switcher.
- [x] Add a Korean locale bundle with the same key coverage as English.

## 2. Feature Surface Migration

- [x] Migrate remaining Accounts, Dashboard, API Keys, APIs, Reports, Automations, Firewall, Model Sources, Quota Planner, Sticky Sessions, and Upstream Proxy user-facing strings to i18n keys.
- [x] Fill Simplified Chinese translations for the newly migrated strings.
- [x] Preserve English for technical terms where that is the more natural Korean/Chinese dashboard wording.

## 3. Verification

- [x] Compare locale key coverage across `en`, `zh-CN`, and `ko`.
- [x] Review Korean and Simplified Chinese translations in multiple passes for natural wording and terminology consistency.
- [x] Run relevant frontend lint/type/test validation without exercising real reset-credit redemption or other external side effects.
