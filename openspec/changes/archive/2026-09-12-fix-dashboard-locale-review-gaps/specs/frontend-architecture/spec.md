## MODIFIED Requirements

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

#### Scenario: Japanese API-key edit usage labels

- **WHEN** a user selects Japanese and edits an API key with existing limits
- **THEN** current-usage labels translate the limit type, daily/weekly/monthly window, and all-model label
- **AND** nonempty model identifiers, compact amounts, currency amounts, and `5h`/`7d` windows retain their existing representation
- **WHEN** the selected language changes while the dialog is open
- **THEN** the current-usage labels update to that language

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

#### Scenario: Reports generation timestamp follows the selected locale

- **WHEN** Japanese is selected on a browser with an English locale and Reports includes a generation timestamp
- **THEN** the visible generation timestamp uses Japanese date/time formatting and the selected 12-hour or 24-hour preference
- **WHEN** the user changes the date display preference to ISO
- **THEN** the timestamp updates to the existing ISO display format without changing the represented instant
- **AND** an absent generation timestamp does not render an as-of label

#### Scenario: Japanese expiry calendar

- **WHEN** a user opens the API-key expiry custom-date calendar with Japanese selected
- **THEN** month, weekday, date-selection, and previous/next-month labels render in Japanese
- **AND** selecting a date retains the existing expiry semantics

#### Scenario: Japanese operational quantities

- **WHEN** a user views compact quantities and USD amounts with Japanese selected
- **THEN** 10,200 renders as `10.2K`, 1,500,000 as `1.5M`, and 1,500,000,000 as `1.5B`
- **AND** 12 USD renders as `$12.00`
