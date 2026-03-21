## Why

`codex-lb` already preserves Codex-specific headers for upstream Responses traffic, but two gaps keep it from cleanly recognizing and optionally presenting a Desktop-like Codex persona:

- native transport auto-detection only allowlists `codex_cli_rs` and `Codex Desktop`, missing the first-party chat/Desktop originators used in `refs/codex`
- the browser OAuth authorize URL hardcodes `originator=codex_cli_rs`, so operators cannot intentionally opt into a Desktop-like upstream login persona

## What Changes

- Expand native Codex originator detection to accept the first-party chat/Desktop identifiers `codex_atlas` and `codex_chatgpt_desktop`
- Add a configurable OAuth authorize originator so operators can keep the default CLI persona or intentionally request a Desktop-like persona
- Add regression coverage for native originator detection and OAuth authorize query construction

## Impact

- Code: `app/core/clients/proxy.py`, `app/core/clients/oauth.py`, `app/core/config/settings.py`
- Tests: `tests/unit/test_proxy_utils.py`, `tests/unit/test_oauth_client.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/outbound-http-clients/spec.md`
