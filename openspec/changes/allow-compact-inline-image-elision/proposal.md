## Why

Long-running image-heavy Codex sessions can reach terminal compaction with a
required latest tool output containing a large inline data-URL image. The model
already observed the image during the live turn, but the compact request must
retain the latest tool call/output pair. Retaining the raw image bytes can exceed
the compact wire cap and permanently terminate an otherwise recoverable thread
with `responses_compact_input_too_large`.

## What Changes

- Replace inline data-URL image parts with an explicit textual omission marker
  only while preparing an oversized compact request.
- Preserve the surrounding tool call/output identities and all textual content.
- Leave file-backed image references and ordinary non-compact requests unchanged.
- Keep fail-closed behavior for required oversized non-image content.

## Impact

- Affected spec: `responses-api-compat`.
- Affected code: compact request preparation in `app/core/openai/requests.py`.
- No schema, account-selection, or normal Responses wire behavior changes.
