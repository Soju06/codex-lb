## ADDED Requirements

### Requirement: Simplified Chinese locale bundle covers all dashboard keys

The `zh-CN` locale bundle SHALL provide an entry for every user-visible i18n
key present in the `en` bundle, so no dashboard surface falls back to English
because of a missing key. Values MAY keep protocol names, product names,
model/API terminology, and compact operational abbreviations in English when
the English form is the clearest operator-facing label.

#### Scenario: zh-CN rendering without English fallback

- **WHEN** a user selects `zh-CN`
- **AND** opens Accounts, Dashboard, API Keys, APIs, Reports, Automations, Firewall, Model Sources, Quota Planner, Sticky Sessions, Upstream Proxy, or Settings subsections
- **THEN** user-visible labels, headings, empty states, dialog copy, accessible labels, and client-side toast fallback copy render through the `zh-CN` bundle
- **AND** no string falls back to English because of a missing locale key
- **AND** technical terms such as `API Key`, `Model`, `OAuth`, `TOTP`, `Credits`, and `Quota` MAY remain English where appropriate

### Requirement: zh-CN terminology stays consistent across feature surfaces

Translated `zh-CN` strings SHALL reuse established dashboard terminology for
repeated concepts, and labels that sit inside a label group whose siblings are
already translated SHALL render in Simplified Chinese as well.

#### Scenario: Consistent wording for repeated concepts

- **WHEN** a concept already has an established `zh-CN` translation on one surface (e.g. 账户消耗预测 in the settings appearance section)
- **THEN** other surfaces referencing the same concept reuse that wording instead of introducing a synonym

#### Scenario: Mixed-label groups render fully in Chinese

- **WHEN** a filter group or table header contains several labels and some already render in Simplified Chinese (e.g. 状态, 类型)
- **THEN** the remaining labels in that group render in Simplified Chinese (e.g. 触发方式) instead of falling back to English
