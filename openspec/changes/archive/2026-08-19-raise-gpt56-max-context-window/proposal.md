## Why

Upstream raised the GPT-5.6 maximum context window from 272,000 to 872,000
tokens while leaving the default input budget at 272,000
(`codex-rs/models-manager/models.json`, openai/codex commit
`2eee483e49f88b868f67364134a658b3298e6c14`, "Raise the GPT-5.6 maximum context
window", openai/codex#39102). codex-lb's bootstrap catalog synthesizes
`max_context_window` as a copy of `context_window`
(`app/core/openai/model_registry.py`), so it advertises a 272,000 ceiling for
Sol, Terra, and Luna. A Codex client pointed at codex-lb before the first live
registry refresh therefore has its `model_context_window` opt-in clamped to
272,000 and cannot reach the window upstream actually serves.

## What Changes

- Decouple `max_context_window` from `context_window` for the GPT-5.6
  bootstrap family: `max_context_window` becomes 872,000; `context_window`
  stays 272,000.
- Keep the GPT-5.6 base provenance pinned at Codex `rust-v0.145.0`, with a
  single tracked exception for `max_context_window`, pinned to the upstream
  commit that raised it (no `rust-v*` release tag carries it yet as of
  `rust-v0.148.0-alpha.21`).
- Document the Codex CLI opt-in, including the clamp semantics that make
  `model_context_window = 1000000` resolve to 872,000 and
  `model_auto_compact_token_limit = 900000` a no-op (Codex clamps the
  auto-compact limit to 90% of the resolved window).

## Non-goals

- `context_window` stays 272,000. It is the tuned default input budget and the
  upstream long-context pricing threshold.
- The other post-`rust-v0.145.0` upstream deltas to these entries
  (`include_apps_usage_instructions`, `include_plugin_usage_instructions`, the
  `base_instructions` relocation to `prompt.md`, `supports_parallel_tool_calls`
  now serde-defaulted) are out of scope and need their own compatibility
  review.
- No clamp or override logic changes; `/v1` input budget fields keep reporting
  the default input budget (see PR #1808 for override plumbing work).

## Impact

- No schema, route, or database migration change.
- Before a live registry refresh, `GET /backend-api/codex/models` changes the
  GPT-5.6 advertised `max_context_window` from 272,000 to 872,000 tokens;
  `context_window` is unchanged.
- `GET /v1/models` is unchanged: it reports the default input budget and does
  not promote `raw["max_context_window"]`.
- Operator `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` entries and persisted
  registry snapshots continue to take precedence over bootstrap values.
- The delta restates the `model-catalog-compat` GPT-5.6 requirement in full,
  so it is order-insensitive with respect to the still-unarchived
  `fix-gpt56-context-window` delta (issue #1714): applied before or after it,
  the merged requirement text is identical.
- Revert cost is one line if upstream reverts before tagging a release.
