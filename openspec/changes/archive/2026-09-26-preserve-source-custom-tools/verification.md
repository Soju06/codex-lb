## Verification (2026-09-26)

### Completeness

The source capability resolver derives `custom` from the two existing capability declarations. Main requirements and context are synced with the change. All implementation and operational checks are complete.

### Correctness

- Before the code fix, the new backend route regression failed because only `wait` survived the filter instead of `exec`, `apply_patch`, and `wait`.
- `uv run pytest -q tests/integration/test_api_keys_api.py -k responses_custom_tools_follow_source_capabilities --maxfail=1`: **40 passed**, covering both routes and trailing slashes, inferred/explicit support, plain and unrelated-capability negatives, grammar preservation, named and allowed choices, and custom call SSE.
- The broader run of `test_api_keys_api.py`, `test_model_sources_catalog.py`, and `test_subscription_overflow_settings.py` passed all **170 existing tests**. Its only 12 failures were new public-route SSE assertions against the initial incomplete test fixture; adding response-created and item-added events corrected the fixture, and all 40 new cases passed in the subsequent run above. No application change was needed for those fixture failures.
- `uv run pytest -q tests/unit/test_replay_safety_portability.py`: **89 passed**.
- Ruff lint and format checks passed for the changed Python files; `git diff --check` passed.
- Strict validation passed for this change and all **65 main specs**.

### Live repair

Appended `custom` to the existing experimental supported-tool lists for `ch/linxaq` and `ch/3sc1a4` through `ModelSourcesService.update_source`, preserving the remaining model fields. The running blue filter and a separate green replica both confirmed custom support.

An ephemeral Codex CLI 0.157.1 run with `ch/linxaq` and the existing HTTP profile read a random marker from `input.txt` through shell, created `output.txt` through apply-patch, and read it back through shell. Both shell commands returned zero, the file-change event completed, and an independent check confirmed the output marker. Evidence is in `/tmp/codex-custom-tools-smoke-s0x0zqjz/retry-events.jsonl` on the operator host.

The first CLI attempt used workspace-write sandboxing, which failed before shell execution with the host's `bwrap: loopback: Failed RTM_NEWADDR` error. The successful retry used the same danger-full-access policy as the reported session and confined file operations to the disposable workspace.

### Coherence and limits

The change uses the existing shared capability resolver and source projection, retaining explicit opt-ins for plain sources and hosted tools. There are no migrations, new settings, or unresolved verification findings. The live metadata repair is effective immediately; the source-code change remains local for a later authorized deployment. Existing unrelated working-tree edits were preserved, and no deployment, commit, or push was performed during implementation.

### Pre-commit validation

The staged change was exported into a detached checkout without unrelated working-tree edits. `uv sync --frozen`, `make lint` (including architecture checks), `uv run ty check`, the 145 model-source/overflow/portability unit tests, all 40 custom-tool API regression cases, and strict validation of all 65 main specs passed.

The required `uv run pre-commit run local-ci --hook-stage manual --all-files` was attempted but stopped at frontend dependency installation because `bun` is unavailable on this host. The full CI suite has therefore not been verified locally.
