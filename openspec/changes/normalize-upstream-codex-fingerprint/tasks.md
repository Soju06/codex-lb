# Tasks

## Implementation
- [x] T1: Add `CodexVersionCache.cached_version_or_default()` synchronous read
  path in `app/core/clients/codex_version.py` (returns cached version if set,
  else `get_settings().model_registry_client_version`; no await, no network).
- [x] T2: Add `codex_fingerprint_os`, `codex_fingerprint_arch`,
  `codex_fingerprint_terminal` settings in `app/core/config/settings.py`
  (defaults `Mac OS 26.5.0` / `arm64` / `iTerm.app/3.6.10`).
- [x] T3: Add `build_codex_user_agent(version)` helper in
  `app/core/clients/proxy.py` producing
  `codex_cli_rs/<version> (<os>; <arch>) <terminal>` (defensive settings access).
- [x] T4: Add native-client detection by User-Agent prefix
  (`_is_native_codex_user_agent` / `_is_native_codex_request`) combined with the
  existing `_has_native_codex_transport_headers`.
- [x] T5: Add `_normalize_non_native_upstream_fingerprint(headers)` and call it
  from `_build_upstream_headers` for non-native http requests: rewrite
  `User-Agent`, strip `x-openai-client-*`, strip inbound `originator`, set
  PascalCase `ChatGPT-Account-Id`.

## Tests
- [x] T6: `tests/unit/test_codex_version.py` — `cached_version_or_default()`
  returns cached value when warmed and settings default when empty; no
  network/await.
- [x] T7: `tests/unit/test_proxy_upstream_fingerprint.py` — non-native http UA
  (`OpenAI/Python 2.24.0`) rewritten to `codex_cli_rs/<ver> (...)`.
- [x] T8: native UA (`codex_exec/...`, `Codex Desktop/...`) left unchanged.
- [x] T9: `x-openai-client-*` headers and inbound `originator` stripped on
  non-native http; no `originator` header added.
- [x] T10: account header emitted as PascalCase `ChatGPT-Account-Id`.
- [x] T11: websocket header builder untouched (native path regression guard).

## Spec
- [x] T12: Add the delta in
  `openspec/changes/normalize-upstream-codex-fingerprint/specs/outbound-http-clients/spec.md`.

## Validation
- [x] T13: `openspec validate normalize-upstream-codex-fingerprint --strict` → valid.
- [x] T14: Targeted pytest — new fingerprint + version-cache suites — 27 passed.
- [x] T15: Broader proxy-client sweep (1006 passed, 3 skipped, 0 new failures;
  18 pre-existing assertions updated for the intended behavior change).
- [x] T16: `uvx ruff check .` + `uvx ruff format --check .` + `uv run ty check` clean.
