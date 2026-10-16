# Context: keep-replayed-history-images-on-http-bridge

Normative text lives in the delta `specs/responses-api-compat/spec.md`. The
stable provenance (requirement history, example, failure modes) now lives in
the capability context, `openspec/specs/responses-api-compat/context.md`, under
"Image-bypass requirement history". This file records only the transient
ordering risk, which task 3.1 closed.

## Conflicts with sibling requirements (resolved)

- Sibling change `narrow-input-image-upstream-transport-pin` (landed as
  `cb21daaed`) carried a `MODIFIED` delta of this same requirement with the old
  "any `input_image` part" trigger. Archiving it after this change would have
  synced the old text back over the new text: the same flip-back mechanism as
  the hamburger regression. It is now archived as
  `openspec/changes/archive/2026-10-16-narrow-input-image-upstream-transport-pin`,
  before this change's requirement was synced into the main spec, so its stale
  delta can no longer be applied. The recorded order is:
  `narrow-input-image-upstream-transport-pin` archived first, this change's
  requirement sync second.
- The precedence item 2 and external-URL scenarios in `Requirement:
  Downstream-HTTP upstream transport follows a configurable policy` govern the
  *upstream transport*, not the bridge. They agree with this change and stay
  unchanged.
- `docs/routing.md` said "image-capable requests … also bypass the bridge";
  task 1.4 updated that sentence to the current-turn boundary.
