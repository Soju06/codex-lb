# Verification

## Completeness and correctness

All five tasks are complete. Both added requirements are implemented.
Source base instructions are projected by `app/modules/model_sources/catalog.py`;
the same module derives namespace support from a non-blank multi-agent version.
The existing request filter preserves the complete declared namespace schema
and matching choices. Generic sources keep their conservative defaults.

Regression coverage includes both catalog routes, both HTTP Responses routes
with and without trailing slashes, v1/v2 capability declarations, malformed
versions, and continued filtering of undeclared tools.

## Validation results

- Focused source/catalog/Responses suite: 83 passed, 137 deselected.
- Ruff check and formatting check: passed on the four touched Python files.
- `ty check app/modules/model_sources/catalog.py`: passed.
- `git diff --check`: passed.
- Strict validation of this OpenSpec change: passed.
- Whole-repository strict spec validation: 50 passed, 15 failed both before
  and after this change. The existing error paths and messages are identical;
  this change adds no new validation errors.

## Live verification

Restored capability metadata for the six affected custom source models and
the local `model_catalog_json` file, retaining the existing HTTP transport and
source credential. The running replicas use their supported explicit
`namespace` opt-in; application code has not been redeployed.

Codex CLI 0.156.1 spawned a real child using `ch/linxaq` and the
`codex-lb-http` provider. The child completed with `CHILD_OK` and its parent
completed with `SUBAGENT_OK`. Both model/provider choices and the parent-child
relationship were confirmed in persisted session records, not just the
parent's final text. Live source requests report `openai_compatible_http`.

An initial `--ephemeral` test failed locally with `no thread with id` when
forking its parent. Repeating with an ordinary persisted session succeeded.
The upstream also rejects a fabricated reserved collaboration schema, so the
successful smoke test used Codex's own tool declarations.
