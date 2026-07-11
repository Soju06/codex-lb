# Tasks

## 1. Spec deltas

- [ ] 1.1 Write delta for `claude-oauth-pool/spec.md` (MODIFIED Requirement) pinning the authorize endpoint + redirect URI defaults and the `code=true` flag.
- [ ] 1.2 Write delta for `clipboard-copy-fallback/spec.md` (MODIFIED Requirement) making the shared-utility requirement explicit for operator-facing controls.

## 2. Settings defaults (TDD)

- [ ] 2.1 Failing test: `tests/unit/test_claude_oauth_service.py::test_default_settings_pin_claude_code_compatible_endpoints` — asserts `Settings().claude_oauth_redirect_uri == "https://platform.claude.com/oauth/code/callback"` and `claude_oauth_authorize_endpoint == "https://claude.com/cai/oauth/authorize"`.
- [ ] 2.2 Fix: update `app/core/config/settings.py` defaults.
- [ ] 2.3 Confirm `tests/unit/test_claude_oauth_service.py` is green.

## 3. URL builder (TDD)

- [ ] 3.1 Failing test: `tests/unit/test_claude_oauth_service.py::test_start_oauth_emits_claude_code_cli_url` — using the production defaults, asserts `authorization_url.startswith("https://claude.com/cai/oauth/authorize?code=true&")` and that `redirect_uri` query parameter equals `https://platform.claude.com/oauth/code/callback`.
- [ ] 3.2 Fix: in `app/modules/claude/oauth/service.py::start_oauth`, add `"code": "true"` as the first entry of the `params` dict.
- [ ] 3.3 Confirm pre-existing `test_start_oauth_returns_authorization_url_with_pkce` still passes (its simulated settings have a different authorize endpoint, so it should be unaffected).

## 4. Frontend Copy button (TDD)

- [ ] 4.1 Failing test: `frontend/src/features/claude/components/add-claude-account-oauth-dialog.test.tsx` — mount the dialog, mock `navigator.clipboard.writeText` to reject, click the Copy button, assert a toast.error is shown and no exception escapes. Use the shared `<CopyButton>` rendering path.
- [ ] 4.2 Fix: in `frontend/src/features/claude/components/add-claude-account-oauth-dialog.tsx`, replace the inline `onClick={() => { void navigator.clipboard.writeText(...) }}` button with the shared `<CopyButton value={startData.authorizationUrl} label={t("claude.oauth.step1.copy")} />` component. Keep the same i18n key.
- [ ] 4.3 Run `pnpm test -- add-claude-account-oauth-dialog` and confirm green.

## 5. Documentation

- [ ] 5.1 Update `.env.example` to reflect the new defaults (with a brief comment about Anthropic's whitelist).
- [ ] 5.2 Add a one-paragraph note to `openspec/changes/fix-claude-oauth-link-endpoints/context.md` documenting the empirical evidence (Claude CLI URL + anthropics/claude-code issues #37831 / #39445 / #44719 / #57985).

## 6. Verify

- [ ] 6.1 `uv run pytest tests/unit/test_claude_oauth_service.py -q`
- [ ] 6.2 `uv run ruff check`
- [ ] 6.3 `cd frontend && pnpm test -- add-claude-account-oauth-dialog`
- [ ] 6.4 `openspec validate fix-claude-oauth-link-endpoints --strict --no-interactive`
