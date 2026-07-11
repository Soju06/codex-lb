## Why

The Codex client model picker requires the `model_messages` field on each
model catalog entry to display newer models (gpt-5.6-sol/terra/luna). Without
it, the client silently drops models absent from its bundled metadata, so
gpt-5.6 models never appear in the picker even though codex-lb is serving
them.

`model_fetcher.py` explicitly stripped `model_messages` from upstream entries
via `_FILTERED_FIELDS = {"model_messages"}`. PR #331 already removed
`base_instructions` from the same filter for an identical reason but left
`model_messages` behind. The `add-gpt56-bootstrap-models` change (PR #1176)
states that `model_messages` is "left to the live registry refresh" — this
change makes that contract true by removing the filter that prevented it.

## What Changes

- Stop filtering `model_messages` from upstream model catalog entries during
  fetch parsing. All upstream fields are now preserved verbatim in
  `UpstreamModel.raw`.
- Remove the now-dead `_FILTERED_FIELDS` abstraction; replace the filter
  comprehension with a direct shallow copy (`raw = dict(data)`).

## Impact

- `GET /backend-api/codex/models` and `GET /v1/models?client_version=<v>` now
  include `model_messages` on each model entry, matching the upstream ChatGPT
  backend response field-for-field.
- Catalog responses are larger (the `model_messages` object contains
  instruction templates, ~16 KB per model). This is inherent to the
  compatibility fix — the client needs the field.
- No database migration; the registry is in-memory only.
- No new security exposure: `model_messages` is upstream catalog content the
  Codex client already receives when talking directly to ChatGPT's backend.
