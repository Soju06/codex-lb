# Verification

## Completeness

The HTTP-only change is implemented in the owner context helper and the existing durable proof, fresh bridge, owner-forward and stale-anchor paths. The canonical requirement matches the delta exactly once. No account-neutral classifier, WebSocket behavior, setting, dependency or schema is changed.

## Correctness

- The new fresh-bridge route regression fails on unchanged main `72630bbfe` because the replacement request still contains the old `previous_response_id`.
- The final focused regression selection passes 60 tests, including the new proof and route suites, existing Responses-Lite developer interleave cases and existing quarantine cases.
- The broader final selection passes 1,528 tests, with one baseline timeout deliberately excluded after reproducing it on unchanged main. It covers replay safety, bridge classification/rejection and the complete remaining HTTP bridge integration suite.
- Tests cover both public HTTP paths, native function/custom-tool metadata, leading historical controls, commentary, full input/cache preservation, subsequent continuation, missing/duplicate/parallel results, invalid metadata/commentary and invalid proof identities.
- Real account selection tests prove an alternate account can serve unrelated work but cannot receive this owner-bound history when its owner is paused, with and without quarantine.
- Existing operation fencing, prior replay and visible-output rejection are covered. The original account-neutral replay tests remain unchanged and pass.

Commands: `uv run pytest tests/unit/test_http_owner_replay.py tests/integration/test_http_native_anchor_recovery.py tests/integration/test_http_native_owner_fence.py tests/unit/test_replay_safety.py tests/unit/test_proxy_http_bridge.py tests/unit/test_http_bridge_safe_continuity.py tests/unit/test_http_bridge_replay_rejection.py tests/integration/test_http_responses_bridge.py -q -k 'not test_v1_responses_http_bridge_idle_recovery_hands_reader_to_replacement' --timeout=60 --tb=short --show-capture=no`; `uv run ty check`; affected Ruff lint/format; proxy architecture and cancellation checks; OpenSpec 1.11.0 strict change and canonical validation.

## Coherence and independent review

Independent review covered the committed implementation and its correction. Initial review passed 47 focused cases. Broader testing subsequently exposed two pre-existing developer-boundary expectations that the first candidate relaxed; the correction keeps non-leading controls visible to the old parser. Independent correction review passed 18 tests, including equivalent noncanonical prefix counterexamples, with no remaining actionable findings. No existing test assertion was weakened.

## Full local gate and limits

`uv run pre-commit run local-ci --hook-stage manual --all-files` was run with isolated test storage. Frontend lint/types/build and 1,658 tests, Python lint/types/architecture checks, Rust lint/tests/release build passed. The gate stopped at Rust dependency audit: unchanged `rustls-webpki 0.103.15` triggers `RUSTSEC-2026-0382`, and unchanged `tokio 1.49.0` triggers `RUSTSEC-2026-0386`. The identical audit failures reproduce on unchanged main; Cargo manifests, lockfile and audit policy are unchanged here. Later full-gate targets were not reached, and the full local gate is not claimed green.

`test_v1_responses_http_bridge_idle_recovery_hands_reader_to_replacement` times out at 60 seconds on both candidate and unchanged main. It is the one exclusion from the final broad run, not a passing test. The remaining warning is the existing Starlette/AnyIO deprecation.

All requests use synthetic accounts, isolated databases and controlled upstream transports. This verifies HTTP product paths and ownership, not live-provider behavior or production deployment. Cache-key preservation does not guarantee a provider cache hit.
