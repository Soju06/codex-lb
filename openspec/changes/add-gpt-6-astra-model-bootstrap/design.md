## Context

The bootstrap entry is checked against [OpenAI Codex rust-v0.153.4 models.json](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/models-manager/models.json). Live account catalogs remain authoritative after refresh.

## Decisions

- Reuse the existing bootstrap helper with explicit Astra fields, including `low` default reasoning, null default service tier, and `unified_exec` shell type.
- Retain current main's upstream-metadata pricing snapshot and fallback client version. The degraded-startup scenario therefore stays in the unmodified "Fallback client version covers the bootstrap catalog" requirement of `openspec/specs/model-catalog-compat/spec.md`, and `test_fallback_client_version_covers_every_bootstrap_minimal_client_version` pins it against Astra's `0.153.0`. The former PR pricing block duplicated that owner and disagreed on priority long-context pricing.
- Add Astra to the existing label-normalization base list. Preserve the existing supported suffix vocabulary.

## Risks

A future upstream catalog can change metadata; live refresh supersedes bootstrap data. Instruction payloads remain intentionally omitted, matching other bootstrap models.
