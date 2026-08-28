# Simplified Chinese Locale Completion Context

## Purpose and Scope

This change finishes the existing Simplified Chinese dashboard bundle and
keeps dashboard numeric units locale-independent: compact quantities use
`K`/`M`/`B`, and USD values use `$`. It does not introduce another locale,
add translation keys, or change locale detection and fallback behavior.

The normative coverage and terminology expectations are recorded in the
[frontend architecture delta](specs/frontend-architecture/spec.md).

## Decisions

- Existing Simplified Chinese wording is the terminology baseline for repeated
  dashboard concepts. Reusing that wording avoids showing synonyms for the same
  concept on different pages.
- Familiar protocol, product, model, and API terms may remain in English when
  that is clearer to operators. An English token is therefore not, by itself,
  evidence of an untranslated entry.
- Compact quantities use stable `K`/`M`/`B` units and USD values use `$`
  across locales. This keeps requests, tokens, costs, pool totals, per-account
  balances, pace projections, and configured thresholds directly comparable.
- The change does not add a second glossary or translation runtime alongside
  the current i18n bundle.

## Constraints and Failure Modes

- Key parity and translated copy are separate concerns: a complete key set can
  still produce a mixed-language dashboard when values remain untranslated.
- Repeated concepts can become confusing when individually valid translations
  use inconsistent terminology across pages.
- Translating established technical terms mechanically can make operational
  labels less recognizable, so review distinguishes intentional English terms
  from accidental English prose.
- Locale-native compact notation can change `10.2K` into `1.02万` and `1.5B`
  into `15亿`; locale-native USD formatting can change `$12.00` into
  `US$12.00`. Both obscure direct comparison with logs, thresholds, and
  English-language operational references.

## Example

In the Automations filters, `Status`, `Type`, and `Trigger` appear as one label
group. Rendering them as `状态`, `类型`, and `触发方式` avoids a single English
label inside an otherwise Chinese control. Likewise, the account burn
projection wording reuses `账户消耗预测` wherever that concept appears.
Dashboard quantities retain forms such as `10.2K`, `46.4K`, `1.5B`, and
`$12.00` after the interface switches to Simplified Chinese.
