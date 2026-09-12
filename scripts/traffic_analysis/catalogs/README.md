# Pinned Codex `/models` catalogs

Inputs to `codex_body_capture.py`, which serves one of these to the Codex client
from its loopback origin. The capture lane must pin the catalog because the
Codex model manager invalidates its cache on a `client_version` mismatch and
caches for only 300 s, so every run refetches `/models` from whatever base URL
the provider names — an unpinned catalog would make the capture
non-reproducible.

| File | Fetched (UTC) | Slugs | SHA-256 |
|---|---|---|---|
| `codex-models-20260911.json` | 2026-09-11 | `gpt-5.6-sol`, `gpt-5.5` | `de111010ad347d62a87346760fb6746bc1a490483359b8cefd66e57daa0ed576` |

`codex-models-20260911.json` is the default `--catalog` and is the catalog that
produced the committed fixture corpus: its digest is the `catalog_sha256`
recorded in `tests/fixtures/codex_bodies/provenance.json`, and
`tests/unit/test_codex_body_fixtures.py` pins the two against each other. Edit
it in place and that test fails, which is the intent — a changed catalog is a
different capture.

## Where one comes from

A catalog is a Codex `/models` response, which is exactly what the client
caches as `$CODEX_HOME/models_cache.json` after a successful fetch. Copy that
file; do not hand-write one, because the client deserialises the entries
strictly and a missing field fails the run rather than degrading. The shape is:

```json
{ "models": [ { "slug": "gpt-5.5", "context_window": 272000, "...": "..." } ] }
```

Only `slug` matters for selecting a model with `--model`. Everything else is
metadata, and it does **not** fully determine the captured body: 0.154.0 layers
bundled `model_info` overrides on top of whatever is served (a row saying
`supports_search_tool: false` still produces a body carrying `web_search` and
`tool_search` declarations). That is why the CLI version is the primary
provenance key and the catalog digest is secondary.

## Adding one

Add the file, add a row above, and record its digest in the `provenance.json`
entry of every fixture captured with it. A catalog holds Codex-published model
metadata only — no credentials, no account identifiers, no operator data — and
the fixture privacy scan passes over it with zero findings. Never replace an
existing file: a capture's provenance points at a digest.
