## 1. Spec and Locale Coverage

- [x] Add OpenSpec coverage for completing the Simplified Chinese dashboard locale.
- [x] Verify `zh-CN` key coverage matches `en` and `ko` exactly (no missing or extra keys).

## 2. Translation

- [x] Translate the remaining untranslated entries across accounts, apiKeys, apis, automations, dashboard, firewall, formatters, modelSources, quotaPlanner, stickySessions, upstreamProxy, and common namespaces.
- [x] Unify terminology with existing `zh-CN` wording (e.g. 账户消耗预测) and translate the Automations trigger filter label and runs column header.
- [x] Keep technical terms in English where the `ko` locale does the same.
- [x] Keep dashboard numeric units stable across locales with `K`/`M`/`B`
  compact suffixes and the `$` USD prefix.

## 3. Verification

- [x] Compare locale key coverage across `en`, `zh-CN`, and `ko`.
- [x] Review translations in multiple passes for natural wording and terminology consistency.
- [x] Add regression coverage for locale-independent compact quantity
  formatting.
