## Why

The GPT-5.6 bootstrap catalog was originally pinned to Codex
`rust-v0.144.1`, whose entries reported a 372,000-token context window. The
upstream bundled catalog corrected Sol, Terra, and Luna to 272,000 tokens in
`rust-v0.145.0`. codex-lb must advertise the corrected upstream input budget in
its bootstrap catalog and normative compatibility contract so startup/offline
clients do not overfill requests before the live registry refreshes.

## What Changes

- Re-pin GPT-5.6 bootstrap catalog provenance from Codex `rust-v0.144.1` to
  `rust-v0.145.0`.
- Require `context_window` and `max_context_window` of 272,000 for Sol, Terra,
  and Luna.
- Update regression-test evidence comments to cite the reproducible upstream
  bundled catalog release instead of untracked live-fetch artifacts.

## Impact

- No schema, route, or database migration change.
- Before a live registry refresh, bootstrap `/v1/models` and `/backend-api/codex/models` change the GPT-5.6 advertised context budget from 372,000 to 272,000 tokens.
- Affects `model-catalog-compat` documentation, client setup examples, and GPT-5.6 bootstrap regression coverage.
