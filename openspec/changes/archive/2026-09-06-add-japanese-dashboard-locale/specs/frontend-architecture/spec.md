## MODIFIED Requirements

### Requirement: Dashboard supports runtime locale selection

The dashboard SHALL load translations through `i18next` + `react-i18next`, support `en` (default), `zh-CN`, `ko`, and `ja` locales, persist the user's selection in `localStorage` under the key `codex-lb-language`, and apply the active locale to the document's `lang` attribute. Locale detection SHALL prefer the `lang` query parameter, then a persisted preference, then the browser language. Chinese, Korean, and Japanese base language tags SHALL resolve to `zh-CN`, `ko`, and `ja` respectively, ignoring case and accepting regional suffixes separated by hyphens or underscores. Unsupported languages SHALL fall back to `en`.

#### Scenario: First visit with a Chinese browser

- **WHEN** a user opens the dashboard for the first time with `navigator.language = "zh-CN"` and no persisted preference
- **THEN** the in-scope surface (header, status-bar labels, auth screens) renders in Simplified Chinese
- **AND** `localStorage` contains `codex-lb-language=zh-CN`

#### Scenario: First visit with an unsupported browser language

- **WHEN** a user opens the dashboard without a language override or saved preference using an unsupported browser language such as `fr-FR`
- **THEN** the in-scope surface renders in English
- **AND** the dashboard does not raise locale-loading errors

#### Scenario: User toggles the language

- **WHEN** the user activates the language switcher in the app header and selects `简体中文`
- **THEN** the in-scope surface re-renders in Simplified Chinese without a full page reload
- **AND** `localStorage.codex-lb-language` is set to `zh-CN`
- **AND** `document.documentElement.lang` is set to `zh-CN`

#### Scenario: Selection persists across reloads

- **WHEN** the user reloads the dashboard after selecting a language
- **THEN** the previously selected language is reapplied before the first paint

#### Scenario: First visit with a Japanese browser

- **WHEN** a user opens the dashboard with browser language `ja-JP` and no language override or saved preference
- **THEN** the dashboard renders in Japanese
- **AND** `localStorage.codex-lb-language` and `document.documentElement.lang` are `ja`

#### Scenario: Japanese is selectable on desktop and mobile

- **WHEN** a user chooses `日本語` from either the desktop or mobile language menu
- **THEN** the current screen re-renders in Japanese without reloading
- **AND** the selected language is saved and applied to the document language
- **AND** the user can switch back to any other supported language

#### Scenario: Saved language and explicit overrides take precedence

- **GIVEN** the browser language is `ja-JP`
- **WHEN** the user has previously selected English and opens the dashboard without a `lang` override
- **THEN** English is used
- **WHEN** the user opens the dashboard with `?lang=ja-JP`
- **THEN** Japanese is used and the saved language becomes `ja`

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

#### Scenario: Japanese feature page rendering

- **WHEN** a user selects `ja` and opens any in-scope dashboard page or dialog
- **THEN** user-visible copy renders in Japanese, except for technical terms and user- or server-provided content
- **AND** translation interpolation and inline markup preserve dynamic values and formatting
- **AND** no string falls back to English because a Japanese translation key is missing

#### Scenario: Locale bundles stay in sync

- **WHEN** the frontend locale bundles are compared
- **THEN** `en`, `zh-CN`, `ko`, and `ja` expose the same translation keys
- **AND** each translation preserves the interpolation variables and inline markup of the English source

#### Scenario: Japanese API-key limit summaries

- **WHEN** a user views API-key limits with Japanese selected
- **THEN** daily, weekly, and monthly periods use the translated labels used by the limit editor
- **AND** numeric values and technical window abbreviations such as `5h` and `7d` are preserved

## ADDED Requirements

### Requirement: Japanese locale formats dashboard dates and calendar controls

With Japanese selected and the default date display format active, the dashboard
SHALL format dates and times using the Japanese locale. Explicit ISO date and
12-hour or 24-hour time preferences SHALL retain their existing behavior.
The API-key expiry calendar SHALL render its month, weekdays, and accessible
navigation and day labels in Japanese. Compact quantities and USD amounts SHALL
retain the existing locale-independent `K/M/B` and `$` notation.

#### Scenario: Japanese timestamps respect time preferences

- **WHEN** Japanese is selected with default date formatting and 12-hour time
- **THEN** dates use Japanese year/month/day order and times use Japanese day-period labels
- **WHEN** the user switches to 24-hour time or ISO date formatting
- **THEN** the selected format applies without changing the represented instant

#### Scenario: Japanese expiry calendar

- **WHEN** a user opens the API-key expiry custom-date calendar with Japanese selected
- **THEN** month, weekday, date-selection, and previous/next-month labels render in Japanese
- **AND** selecting a date retains the existing expiry semantics

#### Scenario: Japanese operational quantities

- **WHEN** a user views compact quantities and USD amounts with Japanese selected
- **THEN** 10,200 renders as `10.2K`, 1,500,000 as `1.5M`, and 1,500,000,000 as `1.5B`
- **AND** 12 USD renders as `$12.00`
