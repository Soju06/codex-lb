## ADDED Requirements

### Requirement: Dashboard supports Korean runtime locale

The dashboard SHALL support Korean (`ko`) as a runtime locale in addition to
English (`en`) and Simplified Chinese (`zh-CN`). Korean language detection SHALL
select `ko` for browser language tags whose base language is `ko`, and the
language switcher SHALL let users choose Korean without reloading the page.

#### Scenario: First visit with a Korean browser

- **WHEN** a user opens the dashboard for the first time with `navigator.language = "ko-KR"` and no persisted preference
- **THEN** the dashboard renders the translated in-scope surface in Korean
- **AND** `localStorage` contains `codex-lb-language=ko`
- **AND** `document.documentElement.lang` is set to `ko`

#### Scenario: User toggles Korean

- **WHEN** the user activates the language switcher and selects Korean
- **THEN** the dashboard re-renders translated strings in Korean without a full page reload
- **AND** the selected language persists across reloads

### Requirement: Dashboard feature surfaces render in the active locale

Dashboard feature surfaces SHALL render user-visible copy through the active
i18n locale, including page headings, section headings, empty states, table
headings, filter labels, button labels, accessible labels, dialog titles,
dialog descriptions, validation messages, and client-side toast fallback copy.
This requirement applies to Accounts, Dashboard, API Keys, APIs, Reports,
Automations, Firewall, Model Sources, Quota Planner, Sticky Sessions, Settings
subsections, and shared dashboard components.

The dashboard MAY keep protocol names, product names, model/API terminology,
quota window abbreviations, and compact operational abbreviations in English
when the English form is the clearest operator-facing label.

#### Scenario: Korean feature page rendering

- **WHEN** a user selects `ko`
- **AND** opens Accounts, Dashboard, API Keys, APIs, Reports, Automations, Firewall, Model Sources, Quota Planner, Sticky Sessions, or Settings subsections
- **THEN** user-visible labels, headings, empty states, dialog copy, accessible labels, and client-side toast fallback copy render in Korean
- **AND** technical terms such as `API Key`, `Model`, `TOTP`, `OAuth`, `TTFT`, `TPS`, and `Fast Mode` MAY remain English where appropriate

#### Scenario: Simplified Chinese feature page rendering

- **WHEN** a user selects `zh-CN`
- **AND** opens a dashboard feature page beyond the original auth/header/settings coverage
- **THEN** newly migrated user-visible strings render in Simplified Chinese
- **AND** the page does not fall back to English because a locale key is missing

#### Scenario: Locale bundles stay in sync

- **WHEN** the frontend locale bundles are compared
- **THEN** `en`, `zh-CN`, and `ko` expose the same translation keys
