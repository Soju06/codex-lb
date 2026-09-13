## Why

PR #1661 already landed the backend contract for operator-declared model-source
reasoning efforts. PR #1675 still carries the useful dashboard part of that
feature, but its original branch also duplicated the backend parser/spec work
and baked in assumptions that #1661 explicitly rejected (`none` filtering and a
fixed effort enum).

The dashboard still needs a way to configure the metadata that the backend now
honors, and it needs to preserve arbitrary provider-specific effort slugs rather
than forcing one hardcoded vocabulary.

## What Changes

- Add model-source dashboard controls for the reasoning-effort metadata that
  `#1661` already reads from `raw_metadata_json`.
- Store supported efforts as an operator-edited list of slugs instead of a
  fixed checkbox enum, so the UI can round-trip `none` and provider-specific
  effort names.
- Keep the existing reasoning toggle, seed reasonable defaults when an operator
  enables it for the first time, and normalize stale defaults back onto the
  configured effort list during edit/save.
- Localize the new dashboard copy in `en`, `ko`, and `zh-CN`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `frontend-architecture`: model-source create/edit dialogs can configure and
  preserve supported reasoning-effort metadata for source models.

## Impact

- Dashboard only: model-source form state, create/edit dialogs, i18n, and
  focused frontend regression tests.
- No API contract, database, backend parser, proxy routing, or request-policy
  behavior changes in this PR; those remain owned by #1661.
