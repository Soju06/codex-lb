## Context

See [proposal.md](proposal.md) for the review findings. Both components bypass existing locale resources; the shared formatter and translation bundles already support the required output.

## Goals / Non-Goals

Reuse existing formatting and translation contracts. Preserve timestamp instants, model identifiers, amounts, compact units, and edit payloads.

## Decisions

- Reports uses `formatDateTimeInline` and subscribes to the date display preference, matching existing timestamp consumers. A hardcoded `ja-JP` formatter would ignore other selected languages and the explicit display preferences.
- API-key edit usage labels use the existing `apiKeys.limitTypes`, `apiKeys.limitWindows`, and `apiKeys.modelSelect.all` keys. For example, `Tokens (weekly, all)` becomes `トークン (週次, すべてのモデル)`. No parallel translation map is needed.
- Component regressions cover displayed output and preference/language changes; browser captures use an English browser with Japanese selected and fixture APIs.

## Risks / Trade-offs

Preference stores and i18n are shared across tests; restore their state after localization checks. Keep numeric rendering and nonempty model filters unchanged. Reverting these frontend changes requires no migration.
