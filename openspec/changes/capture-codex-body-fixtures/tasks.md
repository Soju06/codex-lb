## 1. Capture lane

- [x] 1.1 Add `scripts/traffic_analysis/codex_body_capture.py`: network-namespace re-exec, in-process origin serving a pinned catalog and the Responses lifecycle, throwaway `CODEX_HOME`, disposable provider token, per-slug/transport/run artifact naming, manifest with SHA-256 attestations, printed summary.
- [x] 1.2 Add the refusals as pure functions raising before any process starts: `assert_output_outside_repo`, `assert_clean_environment` (including the outbound proxy family in both spellings, because the Codex client routes even a loopback POST through `HTTP_PROXY`), `assert_ambient_home_uncredentialed`, `assert_loopback_base_url`, `assert_catalog_path`.
- [x] 1.3 Add behaviour-free public aliases to `origin_fixture` (`decode_request_body`, `response_events`, `sse_frames`, `loopback_host`) instead of duplicating the decode/lifecycle/loopback logic.
- [x] 1.4 Cover the guards and the naming in `tests/unit/test_codex_body_capture_guards.py` (positive and refusing case each). Nothing there starts `codex`, a uvicorn thread or the capture orchestration. `unshare` runs at import (a `skipif` probe) and inside the one test that exercises the isolation verifier from within the namespace it verifies.
- [x] 1.5 Serve both transports the client may choose, keep the turn frame rather than the `generate: false` prewarm, and key every artifact by the transport observed; cover the origin over both transports with a test client in `tests/unit/test_codex_body_capture_origin.py`.
- [x] 1.6 Commit the reference `/models` catalog that produced the corpus as the `--catalog` default, with a catalogs README and a digest pinned against every captured provenance entry.
- [x] 1.7 Make the isolation attestation an observation: carry "already inside the namespace" on the child's command line instead of in an inheritable environment variable, read the namespace's interfaces from the kernel before capturing, refuse when anything but loopback answers, and record the observed list in the manifest as `network_isolation`.

## 2. Rebuild and residual scan

- [x] 2.1 Add `scripts/traffic_analysis/codex_body_sanitize.py`: telemetry drops, identifier placeholders, absence preservation, idempotence, headers sidecar.
- [x] 2.3 Add `scripts/traffic_analysis/fixture_privacy_scan.py`: reuse `privacy_scan.scan_tree` for credential shapes; add the residual pass with value-conditioned patterns so Codex's legitimate numeric `session_id` tool parameter never matches; read the bare-telemetry-key exemptions from `provenance.json` so the documented strict command passes on a pristine checkout with no flags.
- [x] 2.5 Keep the residual pass machine-independent: pin it in the committed gate, and recognise host/account names by shape rather than by asking the running box what it is called, so the gate cannot report an unclearable finding.
- [x] 2.4 Cover removal (against output bytes), preservation (byte equality and absence), idempotence and fail-closed in `tests/unit/test_codex_body_sanitizer.py`.
- [x] 2.6 **Invert the sanitiser from a denylist over free text to an allowlist over structure.** Rebuild every node against a declared rule that either preserves a closed domain the portability decision reads or synthesises the value from its JSON path; refuse anything no rule describes; verify on every run that no captured string outside the allowlist reaches the output, and refuse to write when one does. Two rounds of widening the path/identity regexes were each bypassed; the second was measured (`PATH=/usr/local/bin:/home/jane/.local/bin` in a `function_call_output.output` was rewritten so that the residual scan own trigger disappeared and the operator home directory passed a green gate).
- [x] 2.7 Move the residual patterns out of the sanitiser into `fixture_privacy_scan`, so no pattern both rewrites a captured body and judges it; scan the **captured** body as well as the rebuilt one and label a kind that vanishes as evidence about the rebuild, never as a pass.
- [x] 2.8 Make human diff review the commit gate: writing into `tests/fixtures/codex_bodies/` requires `--i-have-read-the-sanitised-body`, and the docs say plainly that this, not the pattern scan, is the boundary.
- [x] 2.9 Delete the completeness claims the inversion makes false — "anywhere in the file at any depth", "the gate reports it as `operator_path` rather than letting it through", "the privacy gate is the *only* thing standing between a future capture and a committed operator path" — from the docs, the corpus README, the openspec scenario and the test docstrings, and replace them with what is true: a structural allowlist plus an admittedly incomplete residual scan.

## 3. Corpus, provenance and the gate

- [x] 3.1 Capture `gpt-5.5` and `gpt-5.6-sol` bodies with Codex 0.154.0 through the new command; rebuild and commit them. The committed pair carries the shape of the real request and synthetic prose; the gate asserts the recorded verdict is unchanged by the rebuild.
- [x] 3.2 Add `tests/fixtures/codex_bodies/provenance.json` as the machine-readable source of truth, including the expected view and the expected portability verdict per fixture.
- [x] 3.3 Add `tests/fixtures/codex_bodies/README.md`: corpus table, recorded verdicts, the divergence table against Codex 0.154.0, the runbook, the pre-commit checklist, the limitations.
- [x] 3.4 Add `tests/unit/test_codex_body_fixtures.py`: shape through production's `openai_compat` dispatch, recorded-verdict assertion, production cross-pins, files ↔ provenance ↔ README sync, privacy gate.
- [x] 3.5 Plant the mutations (re-added telemetry, live cache key, operator home path, credential shape, removed tool surface, flipped origin, wrong recorded reason, undeclared file) on `tmp_path` copies and assert the shipped gate rejects each.
- [x] 3.8 Plant the adversarial leak shapes the denylist rounds missed — a path after `:` in `PATH=`/`LD_LIBRARY_PATH=`, an `rsync` `account@host:/abs/path` target, `file:///home/...`, a home path inside a nested JSON string in a tool call `arguments`, the same path `\uXXXX`-escaped and base64-wrapped, an email address and an API-key-shaped token — and assert each is moot after the rebuild, including the two no residual pattern recognises at all.
- [x] 3.6 Correct the synthetic pair where it was unemittable (`reasoning_summary_delivery`, `parallel_tool_calls`) and placeholder its identifiers; keep it pre-strip.
- [x] 3.7 Correct the stale `tests/unit/test_model_sources_projection.py` docstring (WP-C2 shipped in #2257; the corpus now gates the production flip).

## 4. Specs and docs

- [x] 4.1 `compatibility-tooling` delta: carve the committed-fixture exception out of the version-control prohibition; add the isolated capture lane requirement with its refusals.
- [x] 4.2 `model-source-routing` delta: the corpus must record provenance and the gate must assert the recorded verdict.
- [x] 4.3 `docs/traffic-parity.md` section, cross-linked with the fixture README, with every command in the `uv run python -m` form the rest of the document already uses (a bare `python` is neither present nor sufficient on a typical host).
- [x] 4.4 `openspec validate capture-codex-body-fixtures --strict`, ruff, ty, targeted suites.
- [x] 4.5 Make `tests/unit/test_codex_body_capture_guards.py` obey its own contract on every host: stub the interface observation where a test must reach or refuse it, so a loopback-only host no longer runs the full orchestration (uvicorn on 19090, `--codex-bin` executed twice) while the isolation-refusal assertion skips itself; reproduced inside `unshare --map-root-user --net`.

## 5. Follow-ups (not this change)

- [ ] 5.1 Owner decision: close the three portability gaps the captures expose (tolerate `tool_search.execution`, accept the real `web_search` declaration fields, strip input-item ids before the overflow decision) or accept that native Codex traffic never overflows. Separate issue and PR.
- [ ] 5.2 Correct `add-subscription-overflow-model-source/design.md` decision 21, whose `{type, description?}` declaration shape is a premise these captures falsify.
- [ ] 5.3 Owner decision: whether a credentialed ChatGPT-auth capture is worth one real upstream request and quota. Recommendation is no for v1; the limitation is recorded in the corpus README.
